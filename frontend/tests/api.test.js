import test from "node:test";
import assert from "node:assert/strict";

const freshApi = () => import(`../src/api.js?test=${Math.random()}`);

test("concurrent requests wait for a single cookie session and preserve the idempotency key", async (t) => {
  const calls = [];
  let releaseSession;
  const sessionReady = new Promise((resolve) => { releaseSession = resolve; });
  t.mock.method(globalThis, "fetch", async (path, options) => {
    calls.push({ path, options });
    if (path === "/api/session") await sessionReady;
    return { ok: true, json: async () => ({ id: "trip-1" }) };
  });
  const { api } = await freshApi();
  const meta = api.meta();
  const created = api.createTrip({ destination_city: "Jaipur" }, "submission-1");
  await Promise.resolve();
  assert.deepEqual(calls.map((call) => call.path), ["/api/session"]);
  releaseSession();
  await Promise.all([meta, created]);
  assert.equal(calls.filter((call) => call.path === "/api/session").length, 1);
  assert.equal(calls.find((call) => call.path === "/api/trips").options.headers["Idempotency-Key"], "submission-1");
  assert.ok(calls.every((call) => call.options.credentials === "same-origin"));
  assert.ok(calls.every((call) => !("X-Client-Id" in (call.options.headers || {}))));
});

test("failed session startup can be retried without sending a trip request", async (t) => {
  let attempts = 0;
  const calls = [];
  t.mock.method(globalThis, "fetch", async (path) => {
    calls.push(path);
    if (path === "/api/session") return { ok: ++attempts > 1 };
    return { ok: true, json: async () => ({ id: "trip-1" }) };
  });
  const { api } = await freshApi();
  await assert.rejects(api.getTrip("trip-1"), /secure session/);
  assert.deepEqual(calls, ["/api/session"]);
  await api.getTrip("trip-1");
  assert.deepEqual(calls, ["/api/session", "/api/session", "/api/trips/trip-1"]);
});

test("SSE opens only after the cookie is ready and cancellation prevents a late connection", async (t) => {
  let releaseSession;
  const ready = new Promise((resolve) => { releaseSession = resolve; });
  t.mock.method(globalThis, "fetch", async () => { await ready; return { ok: true }; });
  const previous = globalThis.EventSource;
  const opened = [];
  globalThis.EventSource = class {
    static CLOSED = 2;
    constructor(url) { opened.push(url); }
    close() {}
  };
  t.after(() => {
    if (previous === undefined) delete globalThis.EventSource;
    else globalThis.EventSource = previous;
  });
  const { streamTrip } = await freshApi();
  const close = streamTrip("trip-1", () => {}, () => {});
  assert.deepEqual(opened, []);
  close();
  releaseSession();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(opened, []);
  const closeAgain = streamTrip("trip-2", () => {}, () => {});
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(opened, ["/api/trips/trip-2/stream"]);
  closeAgain();
});


test("an expired cookie is renewed once before retrying a request", async (t) => {
  let sessions = 0;
  let tripCalls = 0;
  t.mock.method(globalThis, "fetch", async (path) => {
    if (path === "/api/session") { sessions += 1; return { ok: true }; }
    if (++tripCalls === 1) return { ok: false, status: 401 };
    return { ok: true, json: async () => ({ id: "trip-1" }) };
  });
  const { api } = await freshApi();
  assert.deepEqual(await api.getTrip("trip-1"), { id: "trip-1" });
  assert.equal(sessions, 2);
  assert.equal(tripCalls, 2);
});
