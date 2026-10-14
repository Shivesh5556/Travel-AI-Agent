/*
  Shown when the backend does not have the keys it needs.

  This is a deliberate design choice. The earlier version of this project
  would happily produce a plan full of numbers an LLM made up, and nothing on
  screen told you. Now it stops and tells you exactly what is missing, where
  to get it, and what it costs — which is nothing.
*/
export default function SetupNeeded({ missing }) {
  return (
    <div className="card">
      <h3 className="card-title">Two keys and you are running</h3>
      <p className="hint" style={{ marginTop: "-8px", marginBottom: "24px" }}>
        Ghumi Ghumi will not plan a trip on guessed data, so it needs these
        first. Both are free and take about two minutes each.
      </p>

      {missing.map((item, index) => (
        <div
          key={item.key}
          style={{
            borderTop: "1px solid var(--line)",
            paddingTop: "16px",
            marginTop: index === 0 ? 0 : "16px",
          }}
        >
          <div className="day-head">
            <h4 className="num">{item.key}</h4>
            <span className="tag warn">not set</span>
          </div>
          <p style={{ margin: "4px 0" }}>{item.what}</p>
          <p className="hint" style={{ margin: 0 }}>
            {item.cost}{" "}
            <a href={item.where} target="_blank" rel="noreferrer">
              {item.where}
            </a>
          </p>
        </div>
      ))}

      <div
        style={{
          borderTop: "2px solid var(--ink)",
          marginTop: "24px",
          paddingTop: "16px",
        }}
      >
        <p className="eyebrow">Then</p>
        <ol style={{ paddingLeft: "18px", margin: 0 }}>
          <li>
            Open <code className="num">backend/.env</code>
          </li>
          <li>Paste each key after the matching name, no quotes, no spaces</li>
          <li>Stop uvicorn with Ctrl+C and start it again</li>
        </ol>
        <p className="hint" style={{ marginTop: "12px" }}>
          Running out of time before a demo? Set{" "}
          <code className="num">STRICT_MODE=false</code> in the same file and
          it will plan with estimated numbers instead, clearly labelled as
          estimates.
        </p>
      </div>
    </div>
  );
}
