/*
  Every call to the backend goes through this file.

  Vite proxies anything starting with /api to http://127.0.0.1:8000, so we
  never have to write the full URL and CORS never gets in the way during
  development. See vite.config.js.
*/

// A server-issued HttpOnly cookie identifies this browser. JavaScript never
// stores or reads the credential. Share startup work across concurrent calls.
let sessionPromise = null;

export function ensureSession() {
  if (!sessionPromise) {
    sessionPromise = fetch("/api/session", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
    }).then((response) => {
      if (!response.ok) throw new Error("Could not start a secure session. Please retry.");
    }).catch((error) => {
      sessionPromise = null;
      throw error;
    });
  }
  return sessionPromise;
}

async function request(path, options = {}) {
  await ensureSession();
  const activeSession = sessionPromise;
  const fetchOptions = {
    ...options,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  };
  let response = await fetch(path, fetchOptions);
  if (response.status === 401) {
    // A tab may outlive its six-hour cookie. Renew once, sharing concurrent
    // renewals; expired trips still return the server's normal not-found error.
    if (sessionPromise === activeSession) sessionPromise = null;
    await ensureSession();
    response = await fetch(path, fetchOptions);
  }

  if (!response.ok) {
    // FastAPI puts the reason in "detail". For validation errors that is a
    // list of objects, so we pull out the messages.
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") {
        message = body.detail;
      } else if (body.detail && typeof body.detail.message === "string") {
        message = body.detail.message;
      } else if (Array.isArray(body.detail)) {
        message = body.detail.map((d) => d.msg).join(", ");
      }
    } catch {
      // response had no JSON body, keep the generic message
    }
    throw new Error(message);
  }

  return response.json();
}

export const api = {
  meta: () => request("/api/meta"),
  metrics: () => request("/api/metrics"),
  translate: (lang, texts) =>
    request("/api/translate", { method: "POST", body: JSON.stringify({ lang, texts }) }),
  searchCities: (q) => request(`/api/geo/search?q=${encodeURIComponent(q)}`),

  createTrip: (body, idempotencyKey) =>
    request("/api/trips", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(body),
    }),
  getTrip: (id) => request(`/api/trips/${id}`),
  deleteTrip: (id) => request(`/api/trips/${id}`, { method: "DELETE" }),

  runMonitor: (tripId) =>
    request(`/api/monitor/run${tripId ? `?trip_id=${encodeURIComponent(tripId)}` : ""}`, { method: "POST" }),

  startCheckout: (id, traveler) =>
    request(`/api/trips/${id}/checkout`, {
      method: "POST",
      body: JSON.stringify(traveler),
    }),
  verifyPayment: (id, payload) =>
    request(`/api/trips/${id}/verify`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};

/*
  Load Razorpay's checkout widget on demand.

  We do NOT put this script tag in index.html. There is no reason to make
  every visitor download a payment library just to look at an itinerary, and
  the promise below means we only ever load it once no matter how many times
  the user opens checkout.
*/
let razorpayPromise = null;

export function loadRazorpay() {
  if (window.Razorpay) return Promise.resolve(true);
  if (razorpayPromise) return razorpayPromise;

  razorpayPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://checkout.razorpay.com/v1/checkout.js";
    script.onload = () => resolve(true);
    script.onerror = () => {
      razorpayPromise = null;
      reject(new Error("Could not load the Razorpay widget. Check your connection."));
    };
    document.body.appendChild(script);
  });

  return razorpayPromise;
}

/*
  Open the live agent trace.

  EventSource is the browser's built-in Server-Sent Events client. It is one
  line, it reconnects on its own, and it is all we need because the data only
  travels one way (server to browser).

  Returns a close function so the component can clean up on unmount.
*/
export function streamTrip(tripId, onEvent, onDone) {
  let source = null;
  let cancelled = false;
  ensureSession().then(() => {
    if (cancelled) return;
    source = new EventSource(`/api/trips/${tripId}/stream`);
    source.onmessage = (message) => {
      let event;
      try { event = JSON.parse(message.data); }
      catch { return; }
      if (event.step === "__end__") {
        source.close();
        onDone();
        return;
      }
      onEvent(event);
    };
    let failures = 0;
    source.onopen = () => { failures = 0; };
    source.onerror = () => {
      failures += 1;
      if (source.readyState === EventSource.CLOSED || failures >= 5) {
        source.close();
        onDone();
      }
    };
  }).catch(() => { if (!cancelled) onDone(); });
  return () => { cancelled = true; source?.close(); };
}

export function rupees(amount) {
  if (amount === null || amount === undefined) return "—";
  return "₹" + Math.round(amount).toLocaleString("en-IN");
}
