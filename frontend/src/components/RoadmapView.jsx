import { useEffect, useMemo, useState } from "react";
import RouteMap from "./RouteMap.jsx";
import { ConfidenceChip, WhyLine } from "./Why.jsx";
import { useI18n, useTranslated } from "../i18n.jsx";
import { isStopClosedOn, mapsLinkForDay, orderFromHotel, timeAllDays, validateDay, validateMove } from "../lib/itinerary.js";

/*
  The itinerary, drawn as a highway - now with day tabs, clock times, a route
  per day and light editing.

  Editing is deliberately client-side only: drop a stop, move it to another
  day or reorder it, and the times and kilometres re-calculate. It never
  touches the saved plan or the budget on the server (hotel and transport
  costs do not change when you skip a museum). Edits last while the trip is
  on screen, and "Reset" brings the original back.
*/

const WEEKDAYS = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"];

function closedText(days) {
  if (!Array.isArray(days) || days.length === 0) return "";
  return "Closed " + days.map((d) => WEEKDAYS[d]).filter(Boolean).join(" & ");
}

function niceDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
}

export default function RoadmapView({ days, from, to, transport, stay, tripId, onTimedDays }) {
  const { t } = useI18n();
  // Day descriptions, stop blurbs and lunch notes are written per trip, so they
  // go through the translator. Place names stay as they are.
  const { tr } = useTranslated([
    ...(days || []).flatMap((d) => [
      d.description,
      d.lunch?.blurb,
      ...(d.stops || []).flatMap((s) => [s.blurb, s.why]),
      ...(d.extra_ideas || []),
    ]),
  ]);
  const original = useMemo(() => days.map((d) => d.stops || []), [days]);

  // Edits remain in memory; refresh restores the original server plan.
  const [stopsByDay, setStopsByDay] = useState(original);
  const [dropped, setDropped] = useState([]);
  const [active, setActive] = useState(0);
  const [editError, setEditError] = useState("");

  const edited = useMemo(
    () => dropped.length > 0 || JSON.stringify(stopsByDay) !== JSON.stringify(original),
    [dropped, stopsByDay, original]
  );

  const timed = useMemo(
    () => timeAllDays(days.map((d, i) => ({ ...d, stops: stopsByDay[i] || [] })), stay),
    [days, stopsByDay, stay]
  );

  // Let the parent (copy / print) see the edited, timed version.
  useEffect(() => {
    if (onTimedDays) onTimedDays(timed);
  }, [timed, onTimedDays]);

  if (!days || days.length === 0) return null;
  const dayIdx = Math.min(active, days.length - 1);

  function applyEdit(next, target) {
    const errors = validateDay({ ...days[target], stops: next[target] }, stay);
    if (errors.length) { setEditError(errors.join(" ")); return false; }
    setEditError("");
    setStopsByDay(next);
    return true;
  }

  function dropStop(i) {
    const stop = stopsByDay[dayIdx][i];
    setStopsByDay((all) => all.map((s, d) => (d === dayIdx ? s.filter((_, k) => k !== i) : s)));
    setDropped((list) => [...list, { ...stop, fromDay: dayIdx }]);
    setEditError("");
  }

  function restore(k) {
    const stop = dropped[k];
    const compatibility = validateMove(days[stop.fromDay], { ...days[dayIdx], stops: stopsByDay[dayIdx] }, stop, stay);
    if (stop.fromDay !== dayIdx && compatibility.length) { setEditError(compatibility.join(" ")); return; }
    const next = stopsByDay.map((s, d) => d === dayIdx ? orderFromHotel([...s, stop], stay) : s);
    if (applyEdit(next, dayIdx)) setDropped((list) => list.filter((_, i) => i !== k));
  }

  function moveWithin(i, delta) {
    const next = stopsByDay.map((s, d) => {
      if (d !== dayIdx) return s;
      const j = i + delta;
      if (j < 0 || j >= s.length) return s;
      const copy = [...s];
      [copy[i], copy[j]] = [copy[j], copy[i]];
      return copy;
    });
    applyEdit(next, dayIdx);
  }

  function moveToDay(i, target) {
    if (target === dayIdx) return;
    const stop = stopsByDay[dayIdx][i];
    const compatibility = validateMove({ ...days[dayIdx], stops: stopsByDay[dayIdx] },
      { ...days[target], stops: stopsByDay[target] }, stop, stay);
    if (compatibility.length) { setEditError(compatibility.join(" ")); return; }
    const next = stopsByDay.map((s, d) => {
      if (d === dayIdx) return s.filter((_, k) => k !== i);
      if (d === target) return orderFromHotel([...s, stop], stay);
      return s;
    });
    if (applyEdit(next, target)) setActive(target);
  }

  function tabKeyDown(event, index) {
    let target;
    if (event.key === "ArrowRight") target = (index + 1) % timed.length;
    else if (event.key === "ArrowLeft") target = (index - 1 + timed.length) % timed.length;
    else if (event.key === "Home") target = 0;
    else if (event.key === "End") target = timed.length - 1;
    else return;
    event.preventDefault();
    setActive(target);
    document.getElementById(`day-tab-${tripId}-${target}`)?.focus();
  }

  function reset() {
    setStopsByDay(original);
    setDropped([]);
    setEditError("");
  }

  return (
    <div className="card" id="itinerary">
      <div className="section-title-row">
        <div>
          <p className="eyebrow">{t("Itinerary")}</p>
          <h3 className="card-title">{t("Your trip, day by day")}</h3>
        </div>
        {edited && (
          <button className="btn btn-ghost btn-small" onClick={reset}>
            {t("↺ Reset my edits")}
          </button>
        )}
      </div>
      <p className="hint lead-hint">
        {t("Stops are grouped by area and ordered from your hotel so you are not crossing the city twice. Drop, reorder or move stops, and the times re-calculate.")}
        {edited && <strong> {t("Edited view. Copy or print to keep your edits; refresh restores the original plan.")}</strong>}
      </p>

      <div className="roadsign-row">
        <span className="board start">{from}</span>
        {transport && (
          <span className="leg-note">
            {transport.carrier}{transport.duration ? ` · ${transport.duration}` : ""} → {to}
          </span>
        )}
      </div>

      {editError && <div className="error-box" role="alert">{editError}</div>}
      <div className="day-tabs" role="tablist" aria-label="Days">
        {timed.map((d, i) => (
          <button
            key={d.date}
            role="tab"
            id={`day-tab-${tripId}-${i}`}
            aria-controls={`day-panel-${tripId}-${i}`}
            tabIndex={i === dayIdx ? 0 : -1}
            onKeyDown={(event) => tabKeyDown(event, i)}
            aria-selected={i === dayIdx}
            className={`day-tab ${d.is_rainy ? "rainy" : ""}`}
            onClick={() => setActive(i)}
          >
            <span className="dt-n">{t("Day {n}", { n: d.day_number })}</span>
            <span className="dt-d">{niceDate(d.date)}</span>
            {d.rain_chance != null && (
              <span className="dt-r">{d.is_rainy ? "🌧" : "☀️"} {d.rain_chance}%</span>
            )}
          </button>
        ))}
      </div>

      {timed.map((day, panelIdx) => (
      <div key={day.date} className={`day-panel ${day.is_rainy ? "rainy" : ""} ${panelIdx !== dayIdx ? "inactive-day" : ""}`}
        role="tabpanel" id={`day-panel-${tripId}-${panelIdx}`} aria-labelledby={`day-tab-${tripId}-${panelIdx}`}
        tabIndex={panelIdx === dayIdx ? 0 : -1}>
        <h4 className="print-day-title">{t("Day {n}", { n: day.day_number })} · {niceDate(day.date)}</h4>
        <div className="day-stats">
          <span className="tag">{t("{n} km", { n: day.total_km })}</span>
          <span className="tag">~{day.hours} h</span>
          {day.backAt && <span className="tag">{t("back at your stay ~{t}", { t: day.backAt })}</span>}
          {day.indoor_share != null && <span className="tag">{t("{n}% indoor", { n: day.indoor_share })}</span>}
          {day.is_rainy && <span className="tag warn">{t("rain likely")}</span>}
          {day.is_wettest && <span className="tag warn">{t("wettest day")}</span>}
          {mapsLinkForDay(day, stay) && (
            <a className="tag link" href={mapsLinkForDay(day, stay)} target="_blank" rel="noreferrer">
              {t("Open route in Google Maps ↗")}
            </a>
          )}
        </div>

        {day.travel_note && (
          <p className="travel-note">
            <span aria-hidden="true">{transport?.mode === "flight" ? "✈️" : transport?.mode === "bus" ? "🚌" : "🚆"}</span>{" "}
            {tr(day.travel_note)}
          </p>
        )}

        {day.description && <p className="day-desc">{tr(day.description)}</p>}

        {(day.notes || []).map((n) => (
          <p key={n} className="hint nm">ℹ️ {tr(n)}</p>
        ))}

        {day.stops.length > 0 ? (
          <div className="day-grid">
            <ol className="timeline">
              {stay && (
                <li className="tl-item tl-hotel">
                  <span className="tl-time num">{day.startLabel || "9:00 AM"}</span>
                  <span className="tl-dot hotel" aria-hidden="true">H</span>
                  <div className="tl-body">
                    <strong>{t("Start from {name}", { name: stay.name })}</strong>
                  </div>
                </li>
              )}
              {day.stops.map((stop, i) => (
                <li key={stop.name + i} className="tl-item">
                  <span className="tl-time num">{stop.startLabel}</span>
                  <span className="tl-dot" aria-hidden="true">{i + 1}</span>
                  <div className="tl-body">
                    <div className="tl-title">
                      <strong>{stop.name}</strong>
                      {stop.must_see && <span className="tag must-see">★ {t("Must-see")}</span>}
                      <span className="kind">{stop.kind}</span>
                      <ConfidenceChip confidence={stop.confidence} score={stop.score} />
                      {stop.best_time && (
                        <span className="tag">{stop.best_time === "evening" ? t("🌇 best in the evening") : t("🌅 best in the morning")}</span>
                      )}
                      {stop.closed_note && <span className="tag warn">{t(stop.closed_note)}</span>}
                    </div>
                    <div className="tl-meta num">
                      {t("{h}h here", { h: stop.visit_hours })}
                      {stop.leg_km ? ` · ${t("{n} km from previous", { n: stop.leg_km })}` : ""}
                      {` · ${t("until {t}", { t: stop.endLabel })}`}
                    </div>
                    {stop.blurb && <p className="tl-blurb">{tr(stop.blurb)}</p>}
                    <WhyLine why={stop.why} tr={tr} />
                    {stop.recommendation?.cost_note && <p className="hint nm">{t(stop.recommendation.cost_note)}</p>}
                    {!stop.closed_note && closedText(stop.closed_days) && (
                      <p className="hint nm">{t(closedText(stop.closed_days))} · {isStopClosedOn(stop, day.date) ? t("closed on this date — move this stop") : t("planned on an open day")}</p>
                    )}
                    <div className="tl-actions">
                      <button onClick={() => moveWithin(i, -1)} disabled={i === 0} aria-label="Move earlier">▲</button>
                      <button onClick={() => moveWithin(i, 1)} disabled={i === day.stops.length - 1} aria-label="Move later">▼</button>
                      <select
                        value=""
                        onChange={(e) => e.target.value !== "" && moveToDay(i, Number(e.target.value))}
                        aria-label={`Move ${stop.name} to another day`}
                      >
                        <option value="">{t("Move to…")}</option>
                        {timed.map((d, k) => k !== dayIdx && (
                          <option key={d.date} value={k}>{t("Day {n}", { n: d.day_number })}</option>
                        ))}
                      </select>
                      <button className="danger" onClick={() => dropStop(i)}>{t("Drop")}</button>
                    </div>
                  </div>
                  {day.lunchAt && day.lunchAt.after === i && day.lunch && (
                    <div className="tl-lunch">
                      <span className="tl-time num">{day.lunchAt.label}</span>
                      <span className="tl-dot lunch" aria-hidden="true">🍽</span>
                      <div className="tl-body">
                        <strong>{t("Lunch · {name}", { name: day.lunch.name })}</strong>
                        <div className="tl-meta">
                          {day.lunch.blurb ? tr(day.lunch.blurb) : t("nearby")}
                          {typeof day.lunch.distance_km === "number" && (
                            <> · {t("{n} km from the stops", { n: day.lunch.distance_km.toFixed(1) })}</>
                          )}
                          {day.lunch.vegetarian && <> · <span className="tag good">{t("veg")}</span></>}
                          {day.lunch.source === "wikivoyage" && <> · {t("recommended by Wikivoyage travellers")}</>}
                        </div>
                      </div>
                    </div>
                  )}
                </li>
              ))}
            </ol>

            <div className="day-side">
              <RouteMap stay={stay} stops={day.stops} lunch={day.lunch} />
            </div>
          </div>
        ) : (
          <p className="hint">
            {day.travel_day
              ? t("Travel day - no sightseeing planned.")
              : t("Nothing scheduled. A rest day is not the worst idea.")}
          </p>
        )}

        {day.extra_ideas?.length > 0 && (
          <p className="hint">{t("If you have time: {list}", { list: day.extra_ideas.map(tr).join(", ") })}</p>
        )}

        {dropped.length > 0 && (
          <div className="dropped">
            <p className="eyebrow">{t("Dropped stops")}</p>
            <div className="chips small">
              {dropped.map((s, k) => (
                <button key={s.name + k} className="chip" onClick={() => restore(k)} title="Put it back on this day">
                  ↩ {s.name}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
      ))}

      <div className="roadsign-row">
        {transport && <span className="leg-note">{t("{carrier} back to {from}", { carrier: transport.carrier, from })}</span>}
        <span className="board finish">{t("home")}</span>
      </div>

      {stay && (
        <p className="hint">{t("Every day starts and ends at {name}", { name: stay.name })}{stay.area ? `, ${stay.area}` : ""}.</p>
      )}
    </div>
  );
}
