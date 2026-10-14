/*
  Every call to the backend goes through this file.

  Vite proxies anything starting with /api to http://127.0.0.1:8000, so we
  never have to write the full URL and CORS never gets in the way during
  development. See vite.config.js.
*/

/*
  An anonymous id for this browser, so only the browser that planned a trip
  can delete it. Not a login.
*/
function clientId() {
  const KEY = "gg-client-id";
  try {
    let id = localStorage.getItem(KEY);
    if (!id) {
      id = (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`)
        .replace(/[^a-zA-Z0-9-]/g, "");
      localStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    // storage blocked (private mode): a per-tab id still keeps trips separate
    if (!window.__ggClientId) window.__ggClientId = `tab-${Date.now()}`;
    return window.__ggClientId;
  }
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-Client-Id": clientId(), ...(options.headers || {}) },
  });

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

  createTrip: (body) =>
    request("/api/trips", { method: "POST", body: JSON.stringify(body) }),
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
  const source = new EventSource(`/api/trips/${tripId}/stream`);

  source.onmessage = (message) => {
    const event = JSON.parse(message.data);
    if (event.step === "__end__") {
      source.close();
      onDone();
      return;
    }
    onEvent(event);
  };

  // v24: a dropped connection is not "finished". EventSource reconnects by
  // itself (the server replays what was missed and the app drops duplicates
  // by seq). Only after several failures in a row do we stop and hand over to
  // the caller, which then polls the trip until it is done.
  let failures = 0;
  source.onopen = () => {
    failures = 0;
  };
  source.onerror = () => {
    failures += 1;
    if (source.readyState === EventSource.CLOSED || failures >= 5) {
      source.close();
      onDone();
    }
  };

  return () => source.close();
}

/*
  Trips are viewed once and then forgotten. This runs when you plan another
  trip or leave the page. `keepalive` lets the request finish even while the
  tab is closing; errors are ignored (the server also clears old trips itself).
*/
export function forgetTrip(id) {
  try {
    fetch(`/api/trips/${id}`, {
      method: "DELETE",
      keepalive: true,
      headers: { "X-Client-Id": clientId() },
    }).catch(() => {});
  } catch {
    /* nothing to do */
  }
}

export function rupees(amount) {
  if (amount === null || amount === undefined) return "—";
  return "₹" + Math.round(amount).toLocaleString("en-IN");
}
