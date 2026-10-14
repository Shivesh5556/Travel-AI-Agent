/*
  Client-side itinerary maths.

  The backend (dayplanner.py) decides which stops go on which day using
  k-means + nearest-neighbour. This file only does the small bits the UI needs
  AFTER that: when a traveller drops a stop or moves it to another day, we
  re-time the day and re-measure the legs so the numbers on screen stay
  honest. Same formulas as the backend (haversine, 20 km/h on the ground).
*/

const EARTH_KM = 6371;
const DAY_START_HOURS = 9; // 9:00 AM
const GROUND_SPEED_KMH = 20; // same assumption as dayplanner.py
const LUNCH_HOURS = 1;
const LUNCH_AFTER = 12.5; // lunch goes after the first stop that ends past 12:30
const LUNCH_EARLIEST = 12.5; // ...and never before 12:30, even on a short day

export function haversineKm(lat1, lon1, lat2, lon2) {
  const rad = (d) => (d * Math.PI) / 180;
  const dLat = rad(lat2 - lat1);
  const dLon = rad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_KM * Math.asin(Math.sqrt(a));
}

const hasGeo = (p) => p && typeof p.lat === "number" && typeof p.lon === "number";

export function formatClock(hoursFloat) {
  let total = Math.round(hoursFloat * 60);
  const h24 = Math.floor(total / 60) % 24;
  const m = total % 60;
  const suffix = h24 >= 12 ? "PM" : "AM";
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
  return `${h12}:${String(m).padStart(2, "0")} ${suffix}`;
}

/** Re-measure every leg of a day, starting from the hotel. */
export function remeasureDay(stops, stay) {
  let prev = hasGeo(stay) ? stay : null;
  return stops.map((stop) => {
    let leg = stop.leg_km;
    if (prev && hasGeo(stop)) {
      leg = Math.round(haversineKm(prev.lat, prev.lon, stop.lat, stop.lon) * 100) / 100;
    }
    if (hasGeo(stop)) prev = stop;
    return { ...stop, leg_km: leg };
  });
}

/** Order stops by nearest-neighbour from the hotel (same idea as the backend). */
export function orderFromHotel(stops, stay) {
  if (!hasGeo(stay) || stops.some((s) => !hasGeo(s))) return stops;
  const remaining = [...stops];
  const ordered = [];
  let cur = stay;
  while (remaining.length) {
    let best = 0;
    let bestD = Infinity;
    remaining.forEach((p, i) => {
      const d = haversineKm(cur.lat, cur.lon, p.lat, p.lon);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    });
    cur = remaining.splice(best, 1)[0];
    ordered.push(cur);
  }
  return ordered;
}

/** Add clock times, a lunch slot, totals. Returns a fresh day object. */
export function timeDay(day, stay) {
  const stops = remeasureDay(day.stops || [], stay);
  // The backend works out when each day can really start (after the journey
  // on Day 1, for example). Older saved trips do not have it: 9 AM.
  const startHour = typeof day.start_hour === "number" ? day.start_hour : DAY_START_HOURS;
  let clock = startHour;
  let lunchPlaced = !day.lunch;
  let lunchAt = null;
  const timed = [];

  // Day trips: legs longer than 3 km are on real roads (hill roads wind), at
  // the same factor and speed the backend used. City hops stay at 20 km/h.
  const trip = day.day_trip;
  const legHours = (km) =>
    trip && km > 3 ? (km * (trip.factor || 1.35)) / (trip.speed_kmh || 30) : km / GROUND_SPEED_KMH;
  stops.forEach((stop, i) => {
    const travel = legHours(stop.leg_km || 0);
    const start = clock + travel;
    const end = start + (stop.visit_hours || 1.5);
    timed.push({ ...stop, start, end, startLabel: formatClock(start), endLabel: formatClock(end) });
    clock = end;
    const isLast = i === stops.length - 1;
    if (!lunchPlaced && (clock >= LUNCH_AFTER || isLast)) {
      // Never "lunch at 10:00 AM": a short morning waits until lunchtime.
      const at = Math.max(clock, LUNCH_EARLIEST);
      lunchAt = { after: i, start: at, label: formatClock(at) };
      clock = at + LUNCH_HOURS;
      lunchPlaced = true;
    }
  });

  // The way back to the stay counts too.
  const last = stops[stops.length - 1];
  const backKm = last && hasGeo(stay) && hasGeo(last) ? haversineKm(last.lat, last.lon, stay.lat, stay.lon) : 0;
  const backAt = stops.length ? clock + legHours(backKm) : null;
  const legsKm = stops.reduce((s, x) => s + (x.leg_km || 0), 0) + backKm;
  const roadKm = trip
    ? stops.reduce((s, x) => s + ((x.leg_km || 0) > 3 ? (x.leg_km || 0) * (trip.factor || 1.35) : x.leg_km || 0), 0) +
      (backKm > 3 ? backKm * (trip.factor || 1.35) : backKm)
    : legsKm;
  const totalKm = Math.round(roadKm * 10) / 10;
  const hours = stops.length ? Math.round((backAt - startHour - (lunchAt ? LUNCH_HOURS : 0)) * 10) / 10 : 0;

  return {
    ...day,
    stops: timed,
    lunchAt,
    total_km: totalKm,
    hours,
    endsAt: stops.length ? formatClock(clock) : null,
    backAt: backAt != null ? formatClock(backAt) : null,
    startLabel: formatClock(startHour),
  };
}

export function timeAllDays(days, stay) {
  return days.map((d) => timeDay(d, stay));
}

/** Project lat/lon into a box for the schematic route drawing. */
export function projectPoints(points, width, height, pad = 28) {
  const geo = points.filter(hasGeo);
  if (geo.length === 0) return [];
  const lats = geo.map((p) => p.lat);
  const lons = geo.map((p) => p.lon);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const midLat = (minLat + maxLat) / 2;
  const kx = Math.cos((midLat * Math.PI) / 180); // squash longitude so shapes are not stretched
  const spanX = Math.max((maxLon - minLon) * kx, 1e-6);
  const spanY = Math.max(maxLat - minLat, 1e-6);
  const scale = Math.min((width - pad * 2) / spanX, (height - pad * 2) / spanY);
  const offX = (width - spanX * scale) / 2;
  const offY = (height - spanY * scale) / 2;
  return points.map((p) =>
    hasGeo(p)
      ? {
          ...p,
          x: offX + (p.lon - minLon) * kx * scale,
          y: height - (offY + (p.lat - minLat) * scale),
        }
      : null
  );
}

/** A Google Maps directions link for one day: hotel -> stops -> hotel. */
export function mapsLinkForDay(day, stay) {
  const pts = (day.stops || []).filter(hasGeo);
  if (pts.length === 0) return null;
  const ll = (p) => `${p.lat},${p.lon}`;
  const origin = hasGeo(stay) ? stay : pts[0];
  const destination = hasGeo(stay) ? stay : pts[pts.length - 1];
  const mid = (hasGeo(stay) ? pts : pts.slice(1, -1)).slice(0, 9);
  const params = new URLSearchParams({
    api: "1",
    origin: ll(origin),
    destination: ll(destination),
    travelmode: "driving",
  });
  if (mid.length) params.set("waypoints", mid.map(ll).join("|"));
  return `https://www.google.com/maps/dir/?${params.toString()}`;
}

/** Plain-text version of the plan, for copy / share. */
export function planAsText({ trip, days, stay, transport, budget, money }) {
  const lines = [];
  lines.push(`${trip.source_city} → ${trip.destination_city}`);
  lines.push(`${trip.start_date} to ${trip.end_date} · ${trip.travelers} traveller(s)`);
  if (budget) {
    lines.push(
      `Total ${money(budget.total_inr)} of ${money(budget.budget_inr)} budget` +
        ` (${money(budget.per_person_inr)} per person)`
    );
  }
  if (transport) lines.push(`Getting there: ${transport.carrier}, ${transport.duration || ""}`.trim());
  if (stay) lines.push(`Staying at: ${stay.name}${stay.area ? ", " + stay.area : ""}`);
  lines.push("");
  days.forEach((d) => {
    lines.push(`DAY ${d.day_number} · ${d.date} · ${d.total_km} km`);
    d.stops.forEach((s, i) => {
      lines.push(`  ${s.startLabel || ""}  ${i + 1}. ${s.name} (${s.kind}, ${s.visit_hours}h)`);
      if (d.lunchAt && d.lunchAt.after === i && d.lunch) {
        lines.push(`  ${d.lunchAt.label}  Lunch: ${d.lunch.name}`);
      }
    });
    lines.push("");
  });
  lines.push("Planned with Ghumi Ghumi");
  return lines.join("\n");
}
