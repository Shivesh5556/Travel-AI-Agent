import { useState } from "react";
import { useMoney, CurrencyPicker } from "../currency.jsx";
import { TranslationNotice, useI18n, useTranslated } from "../i18n.jsx";
import { planAsText } from "../lib/itinerary.js";
import BudgetPanel from "./BudgetPanel.jsx";
import Checklist from "./Checklist.jsx";
import RoadmapView from "./RoadmapView.jsx";
import StayPicks from "./StayPicks.jsx";
import { ConfidenceChip, WhyLine } from "./Why.jsx";

/*
  The finished plan.

  One rule runs through this whole component: never show a number without
  saying where it came from. A real Duffel fare gets a green "real fare" tag,
  an estimated hotel price gets a plain "estimated" tag. Hiding that
  distinction is exactly what makes travel sites untrustworthy.

  Layout: main column on the left, a sticky summary rail on the right with the
  verdict, currency switch, jump links and actions (copy / print / share).
*/

function SourceTag({ item }) {
  const { t } = useI18n();
  if (!item) return null;
  if (item.is_price_band) return <span className="tag warn">{t("typical rate, not a listing")}</span>;
  const estimated = item.price_is_estimate;
  // Duffel's sandbox returns test inventory (a test airline, GBP prices), so
  // it must never look like a bookable fare to a traveller.
  const sandbox = String(item.source || "").toLowerCase().includes("sandbox");
  if (sandbox) return <span className="tag warn">{t("demo fare (test data)")}</span>;
  return <span className={`tag ${estimated ? "" : "real"}`}>{estimated ? t("estimated price") : t("live fare")}</span>;
}

// True if any part of the plan fell back to the built-in sample data.
function usingSamples(plan) {
  if (plan.stay?.is_price_band) return true;
  if (plan.stay?.source?.includes("offline")) return true;
  return (plan.days || []).some((day) => (day.stops || []).some((stop) => stop.source === "offline-sample"));
}

function niceDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
}

const WEATHER_ICON = (desc = "", rain) => {
  const t = desc.toLowerCase();
  if (t.includes("thunder")) return "⛈️";
  if (t.includes("rain") || t.includes("shower") || t.includes("drizzle") || (rain ?? 0) >= 60) return "🌧️";
  if (t.includes("snow")) return "❄️";
  if (t.includes("fog") || t.includes("mist")) return "🌫️";
  if (t.includes("cloud") || t.includes("overcast")) return "⛅";
  return "☀️";
};


export default function PlanView({ trip, paymentsOn, onCheckout, onRefreshAlerts }) {
  const { money, approx } = useMoney();
  const [timedDays, setTimedDays] = useState(null);
  const [copied, setCopied] = useState("");
  const plan = trip.plan || {};
  const { t } = useI18n();

  // Everything the planner wrote for THIS trip, in one batch, so switching to
  // Hindi is a single request. Hooks must run before any early return below.
  const { tr, status: trStatus, retry: trRetry } = useTranslated([
    plan.reason,
    ...(plan.issues || []).flatMap((i) => [i.issue, i.fix]),
    ...(trip.alerts || []).flatMap((a) => [a.message, a.suggestion]),
    ...(plan.tips || []).flatMap((x) => [x.title, x.text]),
    ...(plan.weather?.days || []).map((d) => d.description),
    ...(plan.weather?.source ? [plan.weather.source] : []),
    ...(plan.stay?.why ? [plan.stay.why] : []),
    ...(plan.stay_options || []).map((x) => x.why),
    ...(plan.preferences?.understood || []),
    ...(plan.transport_note ? [plan.transport_note] : []),
    ...(plan.stay_stretch?.stay?.why ? [plan.stay_stretch.stay.why] : []),
  ].filter(Boolean));

  // The agent could not make this trip work at all.
  if (plan.ok === false) {
    return (
      <div className="card">
        <h3 className="card-title">{t("This trip does not work")}</h3>
        <div className="error-box">{tr(plan.reason)}</div>
        {plan.minimum_needed_inr > 0 && (
          <p className="hint">
            The bare minimum for {trip.travelers} people over these dates is about {money(plan.minimum_needed_inr)}, before
            you get there or sleep anywhere. Try a longer budget, fewer days or a closer destination.
          </p>
        )}
        {plan.attempts && plan.attempts.length > 0 && (
          <ul className="rounds">
            {plan.attempts.map((a) => (
              <li key={a.round} className="failed">
                <span>{a.round}. {a.transport} + {a.stay} ({a.style})</span>
                <span className="amount">{money(a.total_inr)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  if (!plan.days) {
    return (
      <div className="card">
        <h3 className="card-title">{t("Still finishing up")}</h3>
        <p className="hint">
          The trip finished planning but its details have not loaded yet. Please plan it again.
        </p>
      </div>
    );
  }

  const { transport, stay, days, budget, weather, tips, issues } = plan;
  const alerts = trip.alerts || [];

  async function copyText(text, label) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      setTimeout(() => setCopied(""), 2000);
    } catch {
      setCopied("Copy blocked by the browser");
      setTimeout(() => setCopied(""), 2500);
    }
  }

  const copyPlan = () =>
    copyText(planAsText({ trip, days: timedDays || days.map((d) => ({ ...d, stops: d.stops || [] })), stay, transport, budget, money }), t("Plan copied"));

  const jump = (id) => (e) => {
    e.preventDefault();
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="plan-layout">
      <div className="plan-main">
        <TranslationNotice status={trStatus} retry={trRetry} />
        {/* title strip */}
        <div className="plan-title">
          <p className="eyebrow">{trip.start_date} → {trip.end_date} · {trip.travelers} {trip.travelers > 1 ? t("travellers") : t("traveller")}</p>
          <h2>{trip.source_city} <span className="arrow">→</span> {trip.destination_city}</h2>
        </div>

        {/* headline numbers */}
        <div className="stat-row" id="overview">
          <div className="stat"><span className="v">{days.length}</span><span className="k">{t("days")}</span></div>
          <div className="stat"><span className="v">{plan.total_km}</span><span className="k">{t("km on the ground")}</span></div>
        </div>

        {plan.recommendation_summary?.stops > 0 && (
          <div className="reco-banner">
            <strong>{t("Picked for you, not just found nearby")}</strong>
            <span>
              {t("{a} of {b} stops are high-confidence picks · average match {n}/100", {
                a: plan.recommendation_summary.high_confidence,
                b: plan.recommendation_summary.stops,
                n: plan.recommendation_summary.average_score,
              })}
            </span>
          </div>
        )}

        {plan.preferences?.understood?.length > 0 && (
          <div className="card prefs-card">
            <p className="eyebrow">{t("From your notes - applied to this plan")}</p>
            <ul>
              {plan.preferences.understood.map((u) => <li key={u}>{tr(u)}</li>)}
            </ul>
            {plan.transport_note && <p className="hint nm">{tr(plan.transport_note)}</p>}
          </div>
        )}

        {usingSamples(plan) && (
          <div className="notice">
            Some entries below are typical rates and placeholder stops rather than real listings, because none of our free sources
            (OpenStreetMap, Wikipedia, Wikivoyage) had matching data for this route when it was planned. Anything affected is labelled below.
            <div className="fix">
              This can mean the free servers were busy, or that this area genuinely has few named listings. Planning again in a minute sometimes helps with the first; the second usually will not change.
            </div>
          </div>
        )}

        {alerts.length > 0 && (
          <div className="card">
            <p className="eyebrow">{t("Since you planned this")}</p>
            {alerts.map((alert) => (
              <div key={alert.id} className={`notice ${alert.severity === "info" ? "info" : ""}`}>
                {tr(alert.message)}
                {alert.suggestion && <div className="fix">{tr(alert.suggestion)}</div>}
              </div>
            ))}
          </div>
        )}

        {/* Only notes the traveller can act on (budget, altitude, all-day rain,
            a very long journey). Everything the agent can fix, it has fixed. */}
        {issues && issues.length > 0 && (
          <div className="card">
            <p className="eyebrow">{t("Worth knowing before you book")}</p>
            {issues.map((issue, i) => (
              <div key={i} className={`notice ${issue.severity === "info" ? "info" : ""}`}>
                {tr(issue.issue)}
                {issue.fix && <div className="fix">{tr(issue.fix)}</div>}
              </div>
            ))}
          </div>
        )}

        {/* getting there and sleeping */}
        <div className="card" id="stay">
          <p className="eyebrow">{t("Getting there and staying")}</p>
          <div className="duo">
            {transport && (
              <div className="duo-item">
                <span className="duo-icon" aria-hidden="true">{transport.mode?.toLowerCase().includes("flight") ? "✈️" : transport.mode?.toLowerCase().includes("bus") ? "🚌" : "🚆"}</span>
                <div>
                  <div className="day-head"><h4>{transport.carrier}</h4><SourceTag item={transport} /></div>
                  <p className="hint nm">
                    {transport.mode}
                    {transport.duration ? ` · ${transport.duration}` : ""}
                    {transport.stops ? ` · ${transport.stops}` : ""}
                    {transport.depart ? ` · leaves ${transport.depart}` : ""}
                  </p>
                  <p className="price-line">
                    <strong className="num">{money(transport.return_total_inr ?? transport.total_inr * 2)}</strong>{" "}
                    {t("return, for {n}", { n: `${trip.travelers} ${trip.travelers > 1 ? t("travellers") : t("traveller")}` })}
                  </p>
                  <p className="hint nm">
                    {t("{x} per person there and back", { x: money(transport.return_per_person_inr ?? (transport.total_inr * 2) / Math.max(1, trip.travelers)) })}
                    {" · "}
                    {t("{x} each way for everyone", { x: money(transport.one_way_total_inr ?? transport.total_inr) })}
                  </p>
                  <p className="hint nm">{t("Source:")} {transport.source}</p>
                </div>
              </div>
            )}
            {stay && (
              <div className="duo-item">
                <span className="duo-icon" aria-hidden="true">🏨</span>
                <div>
                  <div className="day-head"><h4>{stay.name}</h4><SourceTag item={stay} /></div>
                  <p className="hint nm">{t(stay.kind)} · {stay.area} · {stay.stars}★</p>
                  {stay.score != null && <ConfidenceChip confidence={stay.recommendation?.confidence} score={stay.score} />}
                  {stay.address && <p className="hint nm">{stay.address}</p>}
                  {stay.group_price_per_night_inr && stay.rooms > 1 ? (
                    <>
                      <p className="price-line"><strong className="num">{money(stay.group_price_per_night_inr)}</strong> {t("a night for your group")}</p>
                      <p className="hint nm">{t("{n} {unit}s × {p} each", { n: stay.rooms, unit: t(stay.room_unit || "room"), p: money(stay.price_per_night_inr) })}</p>
                    </>
                  ) : (
                    <p className="price-line"><strong className="num">{money(stay.price_per_night_inr)}</strong> {t("a night")}</p>
                  )}
                  <p className="hint nm">{t("Source:")} {stay.source}</p>
                  <WhyLine why={stay.why} tr={tr} />
                  {plan.stay_ceiling_per_night_inr > 0 && (
                    <p className="hint nm">{t("Your budget leaves up to {n} a night for a stay", { n: money(plan.stay_ceiling_per_night_inr) })}</p>
                  )}
                </div>
              </div>
            )}
          </div>

          {stay && <StayPicks plan={plan} money={money} tr={tr} />}

          {transport && plan.transport_options?.length > 1 && (
            <details className="alts">
              <summary>{t("Other ways to get there")}</summary>
              <ul className="stops">
                {plan.transport_options.filter((o) => o.carrier !== transport.carrier).slice(0, 5).map((option) => (
                  <li key={option.carrier + option.total_inr}>
                    <span className="order">{option.mode}</span>
                    <span>{option.carrier}</span>
                    <span className="kind num">{option.duration} · {money(option.return_total_inr ?? option.total_inr * 2)} {t("return")}</span>
                  </li>
                ))}
              </ul>
              <p className="hint" style={{ marginTop: "8px" }}>
                {trip.travel_style === "budget"
                  ? "You asked for the cheapest option, so the flight was skipped even where one exists."
                  : trip.travel_style === "comfort"
                  ? "You asked for comfort, so a non-stop flight was chosen first and the hotel was trimmed instead."
                  : "The cheapest flight was tried first, then cheaper options until the budget worked."}
              </p>
            </details>
          )}
        </div>

        <BudgetPanel budget={budget} />

        {weather && (
          <div className="card" id="weather">
            <p className="eyebrow">{t("Weather")}</p>
            <p className="hint nm">{tr(weather.source)}</p>
            <div className="wx-row">
              {weather.days.map((day) => (
                <div className="wx" key={day.date}>
                  <span className="wx-d num">{niceDate(day.date)}</span>
                  <span className="wx-i" aria-hidden="true">{WEATHER_ICON(day.description, day.rain_chance)}</span>
                  <span className="wx-t num">{day.low_c}–{day.high_c}°C</span>
                  <span className="wx-s">{tr(day.description)}</span>
                  {day.rain_chance != null && <span className="wx-r num">{t("{n}% rain", { n: day.rain_chance })}</span>}
                </div>
              ))}
            </div>
          </div>
        )}

        <RoadmapView
          days={days}
          from={trip.source_city}
          to={trip.destination_city}
          transport={transport}
          stay={stay}
          tripId={trip.id}
          onTimedDays={setTimedDays}
        />

        <Checklist tripId={trip.id} weather={weather} days={days} />

        {tips && tips.length > 0 && (
          <div className="card" id="tips">
            <p className="eyebrow">{t("What travellers say — retrieved for this destination")}</p>
            <div className="tips-grid">
              {tips.map((tip) => (
                <div className="tip" key={tip.title}>
                  <strong>
                    {tr(tip.title)} {tip.same_city === false && <span className="tag">{t("from {city}", { city: tip.city })}</span>}
                  </strong>
                  <span>{tr(tip.text)}</span>
                </div>
              ))}
            </div>
          </div>
        )}

      </div>

      {/* sticky rail */}
      <aside className="plan-rail">
        {budget && (
          <div className={`rail-card verdict-mini ${budget.fits ? "fits" : "over"}`}>
            <span className="label">{budget.fits ? t("Fits your budget") : t("Over budget")}</span>
            <div className="big num">{money(budget.total_inr)}</div>
            <div className="sub num">
              {budget.fits ? t("{x} left over", { x: money(budget.left_over_inr) }) : t("{x} short", { x: money(budget.over_by_inr) })} · {t("{x} / person", { x: money(budget.per_person_inr) })}
            </div>
          </div>
        )}

        <div className="rail-card">
          <CurrencyPicker />
          {approx && <p className="hint nm">Approximate: fixed indicative rates. The plan itself is in rupees.</p>}
        </div>

        <nav className="rail-card jump" aria-label="Jump to section">
          <p className="eyebrow">{t("Jump to")}</p>
          <a href="#money" onClick={jump("money")}>{t("The money")}</a>
          {weather && <a href="#weather" onClick={jump("weather")}>{t("Weather")}</a>}
          <a href="#itinerary" onClick={jump("itinerary")}>{t("Day by day")}</a>
          <a href="#checklist" onClick={jump("checklist")}>{t("Pack and prep")}</a>
          {tips?.length > 0 && <a href="#tips" onClick={jump("tips")}>{t("Local tips")}</a>}
        </nav>

        <div className="rail-card actions">
          <button className="btn btn-ghost btn-small" onClick={copyPlan}>{t("📋 Copy plan as text")}</button>
          <button className="btn btn-ghost btn-small" onClick={() => window.print()}>{t("🖨 Print / save PDF")}</button>
          <button className="btn btn-ghost btn-small" onClick={onRefreshAlerts}>{t("🌦 Re-check this trip now")}</button>
          {budget?.fits && paymentsOn && (
            <button className="btn btn-small" onClick={onCheckout}>
              {trip.plan?.booking ? t("View demo payment") : t("Demo payment (test mode)")}
            </button>
          )}
          {budget?.fits && !paymentsOn && (
            <p className="hint nm">Add your Razorpay test keys to backend/.env to switch on checkout.</p>
          )}
          <p className="hint nm copied" role="status">{copied}</p>
        </div>
      </aside>
    </div>
  );
}
