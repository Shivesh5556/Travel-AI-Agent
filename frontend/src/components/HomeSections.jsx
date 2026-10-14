import { DESTINATIONS, FAQS } from "../destinations.js";
import { useMoney } from "../currency.jsx";
import { useI18n, useTranslated } from "../i18n.jsx";

/*
  The marketing half of the home page. Pure presentation, no API calls.
  Layout ideas borrowed from trip-planner.in: ready-made destination cards,
  a three-step explainer, a "why use this" grid and an FAQ.
*/

export function PopularTrips({ onPick }) {
  const { money } = useMoney();
  const { t } = useI18n();
  const { tr } = useTranslated(DESTINATIONS.flatMap((d) => [d.name, d.tagline, d.best]));
  return (
    <section className="section" id="popular">
      <div className="section-head">
        <p className="eyebrow">{t("Start from a place")}</p>
        <h2>{t("Popular trips, pre-filled")}</h2>
        <p className="lede-sm">
          {t("Pick one and the planner fills in a sensible trip length, interests and a starting budget. Tweak anything, then let the agent find the real numbers.")}
        </p>
      </div>
      <div className="dest-grid">
        {DESTINATIONS.map((d) => (
          <button key={d.name} className={`dest-card tone-${d.tone}`} onClick={() => onPick(d)}>
            <span className="dest-emoji" aria-hidden="true">{d.emoji}</span>
            <span className="dest-name">{tr(d.name)}</span>
            <span className="dest-tag">{tr(d.tagline)}</span>
            <span className="dest-meta">
              <span>{d.days} {t("days")}</span>
              <span>{t("best {when}", { when: tr(d.best) })}</span>
            </span>
            <span className="dest-budget">
              {t("start from {x} / person", { x: money(d.perPerson) })}
            </span>
          </button>
        ))}
      </div>
      <p className="hint">{t("Starting budgets are suggestions, not quotes. The planner tells you honestly if one is too low.")}</p>
    </section>
  );
}

const STEPS = [
  { n: "1", title: "Name a place and a number", text: "Where from, where to, when, how many of you, and the most you can spend." },
  { n: "2", title: "The agent argues with your budget", text: "It looks up fares, weather and places, checks the total, and re-plans until it fits. Every round is shown." },
  { n: "3", title: "Get a day-by-day road map", text: "Timed stops, a route per day, a cost breakdown and a packing list. Edit it, print it or share the link." },
];

export function HowItWorks() {
  const { t } = useI18n();
  return (
    <section className="section" id="how">
      <div className="section-head">
        <p className="eyebrow">{t("How it works")}</p>
        <h2>{t("From “somewhere nice” to a plan in three steps")}</h2>
      </div>
      <ol className="steps3">
        {STEPS.map((s) => (
          <li key={s.n}>
            <span className="step-stone" aria-hidden="true"><i /><b>{s.n}</b></span>
            <h3>{t(s.title)}</h3>
            <p>{t(s.text)}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

const WHY = [
  { icon: "🎯", title: "Budget is a rule, not a filter", text: "Over by ₹22,000? It swaps the flight for a train and tells you it did." },
  { icon: "🗺️", title: "Days planned by geometry", text: "k-means groups stops by area, nearest-neighbour orders them from your hotel." },
  { icon: "⏱️", title: "Real timings", text: "Every stop gets a clock time, travel legs and a lunch slot. Edit and it re-times." },
  { icon: "🏷️", title: "Honest labels", text: "Live fares, estimated hotel rates and typical bands are tagged. Nothing is hidden." },
  { icon: "🌦️", title: "Weather-aware", text: "Rainy day? Indoor stops move there. A watcher keeps checking after you leave." },
  { icon: "💱", title: "Your currency", text: "Flip the whole plan between ₹, $, €, £, AED and S$ to sanity-check the cost." },
];

export function WhyGhumi() {
  const { t } = useI18n();
  return (
    <section className="section" id="why">
      <div className="section-head">
        <p className="eyebrow">{t("Why Ghumi Ghumi")}</p>
        <h2>{t("Everything a travel agent works out, with the maths on show")}</h2>
      </div>
      <div className="why-grid">
        {WHY.map((w) => (
          <div className="why-card" key={w.title}>
            <span className="why-icon" aria-hidden="true">{w.icon}</span>
            <h3>{t(w.title)}</h3>
            <p>{t(w.text)}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

export function Faq() {
  const { t } = useI18n();
  const { tr } = useTranslated(FAQS.flatMap((f) => [f.q, f.a]));
  return (
    <section className="section" id="faq">
      <div className="section-head">
        <p className="eyebrow">{t("Questions")}</p>
        <h2>{t("Frequently asked")}</h2>
      </div>
      <div className="faq">
        {FAQS.map((f) => (
          <details key={f.q}>
            <summary>{tr(f.q)}</summary>
            <p>{tr(f.a)}</p>
          </details>
        ))}
      </div>
    </section>
  );
}
