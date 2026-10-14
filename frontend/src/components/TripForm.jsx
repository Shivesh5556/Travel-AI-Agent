import { useEffect, useState } from "react";
import CityAutocomplete from "./CityAutocomplete";
import { useMoney } from "../currency.jsx";
import { useI18n } from "../i18n.jsx";

// Dates are built from LOCAL calendar parts. v23 used toISOString(), which
// converts to UTC: in India (UTC+5:30) "midnight on the 23rd" is still the
// 22nd in UTC, so every computed date came out one day early - a 4-day
// popular-trip card filled in a 3-day trip.
function toISODate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

// Ten days out by default, so the weather agent gets a real forecast rather
// than a seasonal estimate. Small thing, but it makes the first demo better.
function defaultDate(daysFromNow) {
  const d = new Date();
  d.setDate(d.getDate() + daysFromNow);
  return toISODate(d);
}

function addDays(iso, n) {
  const [y, m, d] = iso.split("-").map(Number);
  return toISODate(new Date(y, m - 1, d + n));
}

function daysBetween(a, b) {
  const [y1, m1, d1] = a.split("-").map(Number);
  const [y2, m2, d2] = b.split("-").map(Number);
  return Math.round((Date.UTC(y2, m2 - 1, d2) - Date.UTC(y1, m1 - 1, d1)) / 86400000);
}

const STYLES = [
  { id: "budget", icon: "🎒", title: "Squeeze it", blurb: "Cheapest way there, hostels, street food." },
  { id: "balanced", icon: "⚖️", title: "Balanced", blurb: "Fly if it fits, decent room, eat well." },
  { id: "comfort", icon: "✨", title: "Treat myself", blurb: "Non-stop flight and a proper hotel." },
];

const BUDGET_PRESETS = [10000, 25000, 50000, 100000];

function Stepper({ id, label, sub, value, min, max, onChange }) {
  const { t } = useI18n();
  return (
    <div className="stepper-row">
      <div>
        <label htmlFor={id}>{t(label)}</label>
        <span className="hint">{t(sub)}</span>
      </div>
      <div className="stepper">
        <button type="button" onClick={() => onChange(Math.max(min, value - 1))} disabled={value <= min} aria-label={`Fewer ${label}`}>
          −
        </button>
        <output id={id} className="num">{value}</output>
        <button type="button" onClick={() => onChange(Math.min(max, value + 1))} disabled={value >= max} aria-label={`More ${label}`}>
          +
        </button>
      </div>
    </div>
  );
}

export default function TripForm({ interests, onSubmit, busy, prefill }) {
  const { t } = useI18n();
  const { money } = useMoney();
  const [form, setForm] = useState({
    source_city: "Delhi",
    destination_city: "Jaipur",
    start_date: defaultDate(10),
    end_date: defaultDate(13),
    budget_inr: 25000,
    notes: "",
  });
  const [adults, setAdults] = useState(2);
  const [children, setChildren] = useState(0);
  const [picked, setPicked] = useState(["Culture & Heritage", "Food"]);
  const [style, setStyle] = useState("balanced");
  const [error, setError] = useState("");

  // A destination card on the home page pre-fills the planner.
  useEffect(() => {
    if (!prefill) return;
    const start = form.start_date;
    setForm((f) => ({
      ...f,
      destination_city: prefill.name,
      end_date: addDays(start, Math.max(0, prefill.days - 1)),
      budget_inr: prefill.perPerson * (adults + children),
    }));
    setPicked(prefill.interests.filter((i) => interests.includes(i)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill]);

  const travelers = adults + children;
  const today = defaultDate(0);
  const days = Math.max(1, daysBetween(form.start_date, form.end_date) + 1);
  const nights = Math.max(0, days - 1);
  const budget = Number(form.budget_inr) || 0;

  function set(key, value) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function toggleInterest(interest) {
    setPicked((p) => (p.includes(interest) ? p.filter((i) => i !== interest) : [...p, interest]));
  }

  function swap() {
    setForm((f) => ({ ...f, source_city: f.destination_city, destination_city: f.source_city }));
  }

  function submit() {
    setError("");
    if (!form.source_city.trim() || !form.destination_city.trim()) return setError("Both cities are needed.");
    if (form.source_city.trim().toLowerCase() === form.destination_city.trim().toLowerCase())
      return setError("Leaving from and going to are the same city.");
    if (form.start_date < today) return setError("The trip cannot start in the past.");
    if (form.end_date < form.start_date) return setError("The return date is before the start date.");
    if (days > 22) return setError("Trips longer than 21 days are not supported yet.");
    if (budget < 1000) return setError("Budget needs to be at least ₹1,000.");

    // The backend now takes the adult/child split (rooms and hostel beds are
    // priced from it). The short group note stays so the LLM knows too.
    const groupNote =
      children > 0
        ? `Group: ${adults} adult${adults > 1 ? "s" : ""} and ${children} child${children > 1 ? "ren" : ""} (age 2-17).`
        : "";
    const notes = [groupNote, form.notes.trim()].filter(Boolean).join(" ").slice(0, 500);

    onSubmit({
      source_city: form.source_city,
      destination_city: form.destination_city,
      start_date: form.start_date,
      end_date: form.end_date,
      travelers,
      adults,
      children,
      budget_inr: budget,
      interests: picked,
      travel_style: style,
      notes,
    });
  }

  return (
    <div className="card form-card" id="planner">
      <div className="form-head">
        <div>
          <p className="eyebrow">{t("Plan my trip")}</p>
          <h3 className="card-title">So, kahaan chalein?</h3>
        </div>
        <span className="pill">{t(`{days} day${days > 1 ? "s" : ""} · {nights} night${nights !== 1 ? "s" : ""}`, { days, nights })}</span>
      </div>

      {error && <div className="error-box" role="alert">{error}</div>}

      <div className="route-row">
        <CityAutocomplete id="from" label={t("Leaving from")} value={form.source_city} onChange={(t) => set("source_city", t)} placeholder="Dehradun" />
        <button type="button" className="swap-btn" onClick={swap} aria-label={t("Swap cities")} title={t("Swap cities")}>⇄</button>
        <CityAutocomplete id="to" label={t("Going to")} value={form.destination_city} onChange={(t) => set("destination_city", t)} placeholder="Jaipur" />
      </div>

      <div className="field-row">
        <div className="field">
          <label htmlFor="start">{t("Start date")}</label>
          <input id="start" type="date" min={today} value={form.start_date} onChange={(e) => {
            const v = e.target.value;
            setForm((f) => ({ ...f, start_date: v, end_date: f.end_date < v ? v : f.end_date }));
          }} />
        </div>
        <div className="field">
          <label htmlFor="end">{t("Return date")}</label>
          <input id="end" type="date" min={form.start_date} value={form.end_date} onChange={(e) => set("end_date", e.target.value)} />
        </div>
      </div>

      <div className="field travellers">
        <label>{t("Travellers")}</label>
        <div className="traveller-box">
          <Stepper id="adults" label="Adults" sub="Age 18+" value={adults} min={1} max={15} onChange={setAdults} />
          <Stepper id="children" label="Children" sub="Age 2–17" value={children} min={0} max={10} onChange={setChildren} />
        </div>
      </div>

      <div className="field">
        <label htmlFor="budget">{t("Total budget")}</label>
        <div className="budget-input">
          <span className="num">₹</span>
          <input id="budget" type="number" min="1000" step="1000" value={form.budget_inr} onChange={(e) => set("budget_inr", e.target.value)} />
        </div>
        <input
          type="range" min="3000" max="200000" step="1000"
          value={Math.min(Math.max(budget, 3000), 200000)}
          onChange={(e) => set("budget_inr", e.target.value)}
          className="range" aria-label="Budget slider"
        />
        <div className="chips small">
          {BUDGET_PRESETS.map((p) => (
            <button key={p} type="button" className="chip" aria-pressed={budget === p} onClick={() => set("budget_inr", p)}>
              ₹{p.toLocaleString("en-IN")}
            </button>
          ))}
        </div>
        <div className="budget-math">
          <span><strong className="num">{money(budget / travelers)}</strong> {t("per person")}</span>
          <span><strong className="num">{money(budget / travelers / days)}</strong> {t("per person per day")}</span>
        </div>
        <div className="hint">
          {t("For everyone, everything included. This is a hard limit: the planner re-plans until it fits.")}
        </div>
      </div>

      <div className="field">
        <label>{t("What do you actually want to do")}</label>
        <div className="chips">
          {interests.map((interest) => (
            <button key={interest} type="button" className="chip" aria-pressed={picked.includes(interest)} onClick={() => toggleInterest(interest)}>
              {t(interest)}
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <label>{t("How do you want to travel?")}</label>
        <div className="styles">
          {STYLES.map((option) => (
            <button key={option.id} type="button" className="style-card" aria-pressed={style === option.id} onClick={() => setStyle(option.id)}>
              <span className="style-icon" aria-hidden="true">{option.icon}</span>
              <strong>{t(option.title)}</strong>
              <span>{t(option.blurb)}</span>
            </button>
          ))}
        </div>
        <div className="hint">{t("Your budget is still a hard limit either way. This decides what gets given up first if it does not fit.")}</div>
      </div>

      <div className="field">
        <label htmlFor="notes">{t("Anything the planner should know")}</label>
        <textarea id="notes" rows="2" value={form.notes} onChange={(e) => set("notes", e.target.value)} placeholder="No early flights, we are vegetarian, one of us cannot walk far, must visit Nahargarh Fort" />
        <span className="hint">{t("The planner reads this: diet, pace, things to skip and places you must see are all applied.")}</span>
      </div>

      <button className="btn btn-primary btn-wide" onClick={submit} disabled={busy}>
        {busy ? t("Planning…") : t("Make it fit →")}
      </button>
      <p className="hint center">{t("Free to plan · real places and weather · estimates are always labelled")}</p>
    </div>
  );
}
