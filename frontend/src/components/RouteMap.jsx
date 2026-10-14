import { projectPoints } from "../lib/itinerary.js";

/*
  A schematic route for one day, drawn as plain SVG.

  No map tiles and no third-party map library on purpose: it keeps the
  project dependency-free and works offline. It shows the hotel, each stop in
  order, and the leg between them, with longitude squashed by cos(latitude)
  so distances look right. For a street map, the day panel links out to
  Google Maps.
*/
export default function RouteMap({ stay, stops, lunch }) {
  const W = 440;
  const H = 320;
  const points = [];
  if (stay && typeof stay.lat === "number") points.push({ ...stay, _kind: "hotel" });
  stops.forEach((s, i) => points.push({ ...s, _kind: "stop", _n: i + 1 }));
  if (lunch && typeof lunch.lat === "number") points.push({ ...lunch, _kind: "lunch" });

  const projected = projectPoints(points, W, H).filter(Boolean);
  if (projected.length < 2) {
    return (
      <div className="routemap empty">
        <p className="hint">Not enough location data to draw this day.</p>
      </div>
    );
  }

  const hotel = projected.find((p) => p._kind === "hotel");
  const stopPts = projected.filter((p) => p._kind === "stop");
  const lunchPt = projected.find((p) => p._kind === "lunch");
  const path = [hotel, ...stopPts].filter(Boolean).map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");

  return (
    <div className="routemap">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Schematic route for the day">
        <defs>
          <pattern id="rm-grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="var(--line-soft)" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width={W} height={H} fill="url(#rm-grid)" />
        <polyline points={path} fill="none" stroke="var(--asphalt)" strokeWidth="9" strokeLinecap="round" strokeLinejoin="round" opacity="0.92" />
        <polyline points={path} fill="none" stroke="var(--stone)" strokeWidth="2" strokeDasharray="6 7" strokeLinecap="round" strokeLinejoin="round" />
        {lunchPt && (
          <g>
            <circle cx={lunchPt.x} cy={lunchPt.y} r="9" fill="var(--card)" stroke="var(--rust)" strokeWidth="2" />
            <text x={lunchPt.x} y={lunchPt.y + 4} textAnchor="middle" fontSize="11">🍽</text>
          </g>
        )}
        {hotel && (
          <g>
            <rect x={hotel.x - 10} y={hotel.y - 10} width="20" height="20" rx="4" fill="var(--moss)" stroke="var(--asphalt)" strokeWidth="2" />
            <text x={hotel.x} y={hotel.y + 4} textAnchor="middle" fontSize="11" fill="#fff">H</text>
          </g>
        )}
        {stopPts.map((p) => (
          <g key={p._n}>
            <circle cx={p.x} cy={p.y} r="12" fill="var(--stone)" stroke="var(--asphalt)" strokeWidth="2.5" />
            <text x={p.x} y={p.y + 4} textAnchor="middle" fontSize="12" fontWeight="700" fill="var(--asphalt)">{p._n}</text>
          </g>
        ))}
      </svg>
      <div className="routemap-legend">
        <span><i className="lg lg-hotel" /> hotel</span>
        <span><i className="lg lg-stop" /> stop (in order)</span>
        {lunchPt && <span><i className="lg lg-lunch" /> lunch</span>}
        <span className="muted">schematic, not to scale</span>
      </div>
    </div>
  );
}
