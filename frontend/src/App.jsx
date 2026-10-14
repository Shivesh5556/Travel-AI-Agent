import { useEffect, useRef, useState } from "react";
import { api, forgetTrip, streamTrip } from "./api.js";
import { CurrencyProvider } from "./currency.jsx";
import { LanguageSwitcher, useI18n } from "./i18n.jsx";
import Checkout from "./components/Checkout.jsx";
import { Faq, HowItWorks, PopularTrips, WhyGhumi } from "./components/HomeSections.jsx";
import PlanningProgress from "./components/PlanningProgress.jsx";
import PlanView from "./components/PlanView.jsx";
import SetupNeeded from "./components/SetupNeeded.jsx";
import TripForm from "./components/TripForm.jsx";

/*
  The whole app.

  No router library and no saved-trips list. A trip lives only while you are
  looking at it: when you plan another trip, close the tab or refresh, it is
  deleted from the server (see forgetTrip). Nothing is kept for later.
*/

export default function App() {
  return (
    <CurrencyProvider>
      <AppInner />
    </CurrencyProvider>
  );
}

function AppInner() {
  const { t } = useI18n();
  const [config, setConfig] = useState(null);
  const [interests, setInterests] = useState([]);

  const [tripId, setTripId] = useState(null);
  const [trip, setTrip] = useState(null);
  const [events, setEvents] = useState([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [checkingOut, setCheckingOut] = useState(false);
  const [prefill, setPrefill] = useState(null);

  // Load the interest list and what the backend has switched on.
  useEffect(() => {
    api
      .meta()
      .then((data) => {
        setInterests(data.interests);
        setConfig(data.config);
      })
      .catch((e) => setError("Cannot reach the backend. Is uvicorn running? " + e.message));
  }, []);

  // A trip is viewed once: leaving the page (close, refresh, navigate away)
  // deletes it from the server.
  const tripRef = useRef(null);
  useEffect(() => {
    tripRef.current = tripId;
  }, [tripId]);
  useEffect(() => {
    const onLeave = () => {
      if (tripRef.current) forgetTrip(tripRef.current);
    };
    window.addEventListener("pagehide", onLeave);
    return () => window.removeEventListener("pagehide", onLeave);
  }, []);

  /*
    Watch the agent work.

    When tripId changes we open the SSE stream. When the stream ends we fetch
    the finished trip. The returned function closes the stream if the
    component unmounts first - without it you leak a connection every time.
  */
  useEffect(() => {
    if (!tripId) return;

    let cancelled = false;
    let close = () => {};
    let pollTimer = null;

    setEvents([]);
    setRunning(true);
    setTrip(null);
    setCheckingOut(false);

    // Fetch until the trip is no longer "running" (used when the live stream
    // drops for good). Gives up after ~3 minutes.
    async function pollUntilDone(triesLeft = 60) {
      try {
        const fresh = await api.getTrip(tripId);
        if (cancelled) return;
        if (fresh.status === "running" && triesLeft > 0) {
          pollTimer = setTimeout(() => pollUntilDone(triesLeft - 1), 3000);
          return;
        }
        setTrip(fresh);
        setEvents((prev) => (fresh.trace && fresh.trace.length ? fresh.trace : prev));
        setRunning(false);
      } catch (e) {
        if (cancelled) return;
        setRunning(false);
        setError(e.message);
        setTripId(null); // a stale or wrong ?trip= link should not trap the user
      }
    }

    (async () => {
      // Look at the trip FIRST: if it has already finished it is shown
      // straight away instead of waiting on the live stream.
      let existing;
      try {
        existing = await api.getTrip(tripId);
      } catch (e) {
        if (cancelled) return;
        setRunning(false);
        setError(e.message);
        setTripId(null);
        return;
      }
      if (cancelled) return;
      if (existing.status !== "running") {
        setTrip(existing);
        setEvents(existing.trace || []);
        setRunning(false);
        return;
      }

      close = streamTrip(
        tripId,
        (event) =>
          setEvents((previous) => {
            // The stream can replay events after a reconnect (and React
            // StrictMode opens it twice in development). The server numbers
            // each event, so drop anything we already have.
            if (previous.some((e) => e.seq === event.seq)) return previous;
            return [...previous, event];
          }),
        () => pollUntilDone()
      );
    })();

    return () => {
      cancelled = true;
      close();
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [tripId]);

  async function startTrip(body) {
    setError("");
    try {
      const created = await api.createTrip(body);
      setTripId(created.id);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      setError(e.message);
    }
  }

  async function recheck() {
    try {
      await api.runMonitor(tripId);
      setTrip(await api.getTrip(tripId));
    } catch (e) {
      setError(e.message);
    }
  }

  function goHome() {
    if (tripId) forgetTrip(tripId);
    setTripId(null);
    setTrip(null);
    setEvents([]);
    setCheckingOut(false);
  }

  function pickDestination(d) {
    setPrefill({ ...d, nonce: Date.now() });
    document.getElementById("planner")?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  const notReady = config && Array.isArray(config.missing_keys) && config.missing_keys.length > 0;
  const onHome = !notReady && !tripId;

  return (
    <>
      <header className="masthead">
        <div className="masthead-in">
          <button className="wordmark" onClick={goHome} aria-label="Ghumi Ghumi home">
            <div className="milestone-logo" aria-hidden="true"><span className="km">GG</span></div>
            <div className="wordmark-text">
              <h1>Ghumi <span>Ghumi</span></h1>
              <span className="tagline">Bolo kahaan. Baaki hum dekh lenge.</span>
            </div>
          </button>

          <div className="masthead-right">
            <LanguageSwitcher />
          </div>
        </div>
      </header>

      {error && (
        <div className="wrap">
          <div className="error-box" role="alert">
            {error} <button className="link-btn" onClick={() => setError("")}>dismiss</button>
          </div>
        </div>
      )}

      {notReady && <div className="wrap narrow"><SetupNeeded missing={config.missing_keys} /></div>}

      {/* ---------------- home: hero + form ---------------- */}
      {onHome && (
        <>
          <section className="hero">
            <div className="hero-in">
              <div className="hero-copy">
                <p className="kicker">{t("An agent that argues with your budget")}</p>
                <h2>
                  {t("Tell us")} <em>{t("where")}</em>.<br />
                  {t("We'll make it fit")} <em>{t("what you've got")}</em>.
                </h2>
                <p className="lede">
                  {t("Name a place and a number. Ghumi Ghumi looks up real fares, real weather and real places, then keeps re-planning — swapping the flight for a train, the resort for a guesthouse — until the trip actually fits. No “sorry, out of budget” at the last screen.")}
                </p>
                <ul className="usp">
                  <li><span className="usp-n">01</span><div><strong>{t("Budget is a rule, not a filter")}</strong><span>{t("Re-plans until the number works, and shows every round.")}</span></div></li>
                  <li><span className="usp-n">02</span><div><strong>{t("Real places, real fares")}</strong><span>{t("Anything estimated says so on its face.")}</span></div></li>
                  <li><span className="usp-n">03</span><div><strong>{t("Worth your time, with proof")}</strong><span>{t("Every place is scored 0–100 on your interests, quality evidence, cost and route — and the reason is shown.")}</span></div></li>
                  <li><span className="usp-n">04</span><div><strong>{t("Keeps watching")}</strong><span>{t("If the forecast turns, it tells you which days to swap.")}</span></div></li>
                </ul>
              </div>
              <TripForm interests={interests} onSubmit={startTrip} busy={running} prefill={prefill} />
            </div>
            <div className="hero-road" aria-hidden="true" />
          </section>

          <main className="wrap">
            <PopularTrips onPick={pickDestination} />
            <HowItWorks />
            <WhyGhumi />
            <Faq />
          </main>
        </>
      )}

      {/* ---------------- a trip: planning, plan, checkout ---------------- */}
      {!notReady && tripId && (
        <main className="wrap trip-wrap">
          {running && (
            <div className="narrow-card">
              <PlanningProgress events={events} />
            </div>
          )}

          {trip && !checkingOut && (
            <PlanView
              trip={trip}
              paymentsOn={config?.payments}
              onRefreshAlerts={recheck}
              onCheckout={() => setCheckingOut(true)}
            />
          )}

          {trip && checkingOut && (
            <div className="narrow-card">
              <Checkout trip={trip} onDone={async () => setTrip(await api.getTrip(tripId))} />
              <button className="btn btn-ghost btn-small" onClick={() => setCheckingOut(false)}>{t("← Back to the plan")}</button>
            </div>
          )}

          <div className="trip-foot">
            <button className="btn btn-ghost btn-small" onClick={goHome}>{t("+ Plan another trip")}</button>
          </div>
        </main>
      )}

      {/* ---------------- footer ---------------- */}
      <footer className="footer">
        <div className="footer-in">
          <span><strong>Ghumi Ghumi</strong> — ghoomo, budget mein</span>
        </div>
      </footer>
    </>
  );
}
