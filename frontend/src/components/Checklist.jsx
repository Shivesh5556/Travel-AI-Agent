import { useMemo, useState } from "react";
import { useI18n, useTranslated } from "../i18n.jsx";

/*
  "Before you go" checklist.

  Items are derived from the real forecast already in the plan (rain chance,
  highs and lows) plus a short generic list. Nothing is fetched and nothing is
  invented about the destination. Ticks are remembered per trip in this
  browser.
*/
function buildItems(weather, days) {
  const items = [];
  const w = weather?.days || [];
  const maxRain = Math.max(0, ...w.map((d) => d.rain_chance ?? 0));
  const hi = w.length ? Math.max(...w.map((d) => d.high_c ?? -99)) : null;
  const lo = w.length ? Math.min(...w.map((d) => d.low_c ?? 99)) : null;

  if (maxRain >= 50) items.push({ id: "rain", text: `Umbrella or light rain jacket (up to ${maxRain}% rain chance)` });
  if (hi !== null && hi >= 32) items.push({ id: "sun", text: `Sunscreen, cap and a water bottle (highs near ${Math.round(hi)}°C)` });
  if (lo !== null && lo <= 12) items.push({ id: "warm", text: `Warm layers (lows near ${Math.round(lo)}°C)` });
  if ((days || []).some((d) => d.is_rainy)) items.push({ id: "shoes", text: "Shoes with grip, since some days look wet" });

  items.push(
    { id: "id", text: "Government photo ID for every traveller" },
    { id: "tickets", text: "Screenshots of tickets and hotel confirmation (offline)" },
    { id: "maps", text: "Download offline maps for the destination" },
    { id: "power", text: "Power bank and charging cables" },
    { id: "cash", text: "Some cash for small vendors and autos" }
  );
  return items;
}

export default function Checklist({ tripId, weather, days }) {
  const { t } = useI18n();
  const items = useMemo(() => buildItems(weather, days), [weather, days]);
  const { tr } = useTranslated(items.map((i) => i.text));
  // Ticks last while the trip is on screen - nothing about a trip is stored.
  const [done, setDone] = useState([]);

  const toggle = (id) => setDone((d) => (d.includes(id) ? d.filter((x) => x !== id) : [...d, id]));
  const count = items.filter((i) => done.includes(i.id)).length;

  return (
    <div className="card" id="checklist">
      <div className="section-title-row">
        <div>
          <p className="eyebrow">{t("Before you go")}</p>
          <h3 className="card-title">{t("Pack and prep")}</h3>
        </div>
        <span className="pill num">{count}/{items.length}</span>
      </div>
      <ul className="checklist">
        {items.map((item) => (
          <li key={item.id}>
            <label>
              <input type="checkbox" checked={done.includes(item.id)} onChange={() => toggle(item.id)} />
              <span>{tr(item.text)}</span>
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}
