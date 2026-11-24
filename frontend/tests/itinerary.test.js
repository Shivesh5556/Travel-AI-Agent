import test from "node:test";
import assert from "node:assert/strict";
import { isStopClosedOn, timeDay, validateDay, validateMove } from "../src/lib/itinerary.js";

const stay = { lat: 26.9, lon: 75.8 };
const museum = { name: "Museum", ...stay, visit_hours: 1, closed_days: [0] };
const day = (extra = {}) => ({
  date: "2026-10-27", start_hour: 9, available_hours: 7,
  stops: [{ ...museum }], ...extra,
});

test("a stop moved onto its closure weekday is rejected", () => {
  assert.equal(isStopClosedOn(museum, "2026-10-26"), true);
  assert.equal(isStopClosedOn(museum, "2026-10-27"), false);
  assert.match(validateDay(day({ date: "2026-10-26" }), stay).join(" "), /Museum is closed/);
});

test("Sunday uses the same Monday-zero convention as the backend", () => {
  assert.equal(isStopClosedOn({ closed_days: [6] }, "2026-11-01"), true);
});

test("a date-specific event cannot be moved to another date", () => {
  const event = { ...museum, only_on: "2026-10-28" };
  assert.match(validateDay(day({ stops: [event] }), stay).join(" "), /only available on 2026-10-28/);
  assert.deepEqual(validateDay(day({ date: "2026-10-28", stops: [event] }), stay), []);
});

test("travel-only days stay empty", () => {
  assert.match(validateDay(day({ travel_day: true, available_hours: 0 }), stay).join(" "), /no sightseeing time/);
  assert.deepEqual(validateDay(day({ travel_day: true, available_hours: 0, stops: [] }), stay), []);
});

test("the traveller's stop limit is enforced when adding another stop", () => {
  const stops = Array.from({ length: 3 }, (_, i) => ({ ...museum, name: `Museum ${i}`, visit_hours: 0.5 }));
  assert.match(validateDay(day({ max_stops_per_day: 2, stops }), stay).join(" "), /at most 2 stops/);
  assert.deepEqual(validateDay(day({ max_stops_per_day: 3, stops }), stay), []);
});

test("time validation includes lunch, waiting and the return leg", () => {
  const route = day({ available_hours: 4, lunch: { name: "Cafe" } });
  const timed = timeDay(route, stay);
  assert.equal(timed.elapsed_hours, 4.5); // one-hour stop, wait for 12:30 lunch, back at 13:30
  assert.equal(timed.back_at_hour, 13.5);
  assert.match(validateDay(route, stay).join(" "), /only 4 hours are available/);

  const farStop = { ...museum, lat: stay.lat + 0.4 };
  assert.match(validateDay(day({ available_hours: 2, stops: [farStop] }), stay).join(" "), /return to your stay/);
});

test("a visit alone can exceed a short arrival-day window", () => {
  assert.match(validateDay(day({ start_hour: 16, available_hours: 1, stops: [{ ...museum, visit_hours: 2 }] }), stay).join(" "), /only 1 hours/);
});

test("valid days are accepted and validation does not mutate source data", () => {
  const input = day();
  const before = structuredClone(input);
  assert.deepEqual(validateDay(input, stay), []);
  assert.deepEqual(input, before);
});

test("invalid calendar dates fail without a timezone-dependent closure guess", () => {
  assert.match(validateDay(day({ date: "2026-02-31" }), stay).join(" "), /invalid date/);
  assert.equal(isStopClosedOn(museum, "not-a-date"), false);
});


test("moving a hill outing into a city day is rejected and stale metadata never uses city speed", () => {
  const hill = { name: "Hill fort", lat: stay.lat + 0.6, lon: stay.lon, visit_hours: 1 };
  const outing = day({ stops: [hill], day_trip: { factor: 1.6, speed_kmh: 25 } });
  const city = day();
  assert.match(validateMove(outing, city, hill, stay).join(" "), /city sightseeing and day trips/);
  const proposed = timeDay({ ...city, stops: [hill], day_trip: null }, stay);
  assert.equal(proposed.day_trip.factor, 1.6);
  assert.equal(proposed.day_trip.speed_kmh, 25);
  assert.ok(proposed.elapsed_hours > 9, "the remote round trip cannot be represented as a quick city hop");
  assert.equal(timeDay({ ...outing, stops: [{ ...museum }] }, stay).day_trip, null);
});

test("an edited route replaces an unrelated restaurant with an explicit meal break", () => {
  const lunch = { name: "Distant cafe", lat: stay.lat + 0.2, lon: stay.lon };
  const timed = timeDay(day({ lunch }), stay);
  assert.equal(timed.lunch.name, "Lunch break");
  assert.equal(timed.lunch.source, "edited-break");
  assert.equal(timed.lunch.lat, undefined);
  assert.ok(timed.lunchAt, "the meal time is still reserved");
  assert.equal(lunch.name, "Distant cafe");
});
