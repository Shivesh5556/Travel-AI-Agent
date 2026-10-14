import { useMoney } from "../currency.jsx";
import { useI18n, useTranslated } from "../i18n.jsx";

/*
  The money panel.

  A stacked bar showing where the money goes, a "budget used" meter and the
  line-by-line ledger.
*/
const PALETTE = ["#f5b800", "#0f6b57", "#c2410c", "#2b6cb0", "#8a5cc2", "#d6336c", "#6b7280"];

export default function BudgetPanel({ budget }) {
  const { money, approx } = useMoney();
  const { t } = useI18n();
  const { tr } = useTranslated((budget?.lines || []).map((l) => l.label));
  if (!budget) return null;

  const fits = budget.fits;
  const total = budget.total_inr || 0;
  const lines = (budget.lines || []).filter((l) => l.amount_inr > 0);
  const usedPct = budget.budget_inr ? Math.min(100, Math.round((total / budget.budget_inr) * 100)) : 0;

  return (
    <div className="card" id="money">
      <p className="eyebrow">{approx ? t("The money · approx. in your currency") : t("The money")}</p>

      <div className={`verdict ${fits ? "fits" : "over"}`}>
        <div>
          <span className="label">{fits ? t("Fits your budget") : t("Over budget")}</span>
          <div className="big num">{money(total)}</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <span className="label">{fits ? t("Left over") : t("Short by")}</span>
          <div className="big num">{money(fits ? budget.left_over_inr : budget.over_by_inr)}</div>
        </div>
      </div>

      <div className="meter" aria-label={`${usedPct}%`}>
        <div className="meter-fill" data-over={!fits} style={{ width: `${usedPct}%` }} />
      </div>
      <div className="meter-labels num">
        <span>{t("{pct}% of {total} used", { pct: usedPct, total: money(budget.budget_inr) })}</span>
        <span>{t("{x} / person", { x: money(budget.per_person_inr) })}</span>
      </div>

      {total > 0 && lines.length > 0 && (
        <>
          <div className="stack" role="img" aria-label={t("Where the money goes")}>
            {lines.map((l, i) => (
              <span
                key={l.label}
                style={{ width: `${(l.amount_inr / total) * 100}%`, background: PALETTE[i % PALETTE.length] }}
                title={`${l.label}: ${money(l.amount_inr)}`}
              />
            ))}
          </div>
          <ul className="stack-legend">
            {lines.map((l, i) => (
              <li key={l.label}>
                <i style={{ background: PALETTE[i % PALETTE.length] }} />
                {tr(l.label)}
                <span className="num">{Math.round((l.amount_inr / total) * 100)}%</span>
              </li>
            ))}
          </ul>
        </>
      )}

      <table className="ledger">
        <tbody>
          {budget.lines.map((line) => (
            <tr key={line.label}>
              <td>{tr(line.label)}</td>
              <td>{money(line.amount_inr)}</td>
            </tr>
          ))}
          <tr>
            <td>{budget.budget_inr ? t("Total for the whole trip") : t("Total for everything")}</td>
            <td>{money(total)}</td>
          </tr>
        </tbody>
      </table>

      <p className="hint" style={{ marginTop: "12px" }}>
        {tr(`That is ${money(budget.per_person_inr)} per person for the whole trip, or about ${money(budget.per_day_inr)} a day for everyone together. Daily spending assumed at the ${budget.style} level.`)}
      </p>
    </div>
  );
}
