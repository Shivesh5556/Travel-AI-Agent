import { ConfidenceChip, WhyLine } from "./Why.jsx";
import { useI18n } from "../i18n.jsx";

/*
  The other places to sleep, ranked for THIS traveller (budget fit, quality,
  distance to the sights), plus one honest "if you could stretch" suggestion.
  The budget stays the rule: the stretch pick shows exactly what it would cost.
*/
export default function StayPicks({ plan, money, tr }) {
  const { t } = useI18n();
  const picks = (plan.stay_options || []).filter((s) => !s.is_pick && s.name !== plan.stay?.name).slice(0, 3);
  const stretch = plan.stretch || plan.stay_stretch;
  if (picks.length === 0 && !stretch) return null;

  const delta = (n) => (n === 0 ? t("same total") : `${n > 0 ? "+" : "−"}${money(Math.abs(n))}`);

  return (
    <details className="alts">
      <summary>{t("Other stays ranked for you")}</summary>
      {picks.length > 0 && (
        <ul className="stay-picks">
          {picks.map((s) => (
            <li key={s.name + s.lat}>
              <div className="day-head">
                <strong>{s.name}</strong>
                <ConfidenceChip confidence={s.recommendation?.confidence} score={s.score} />
              </div>
              <p className="hint nm">
                {t(s.kind)} · {s.stars}★ · {money(s.group_price_per_night_inr || s.price_per_night_inr)} {t("a night")}{s.rooms > 1 ? ` (${s.rooms} ${s.room_unit}s)` : ""} · {delta(s.delta_total_inr)} {t("for the stay")}
              </p>
              <WhyLine why={s.why} tr={tr} />
            </li>
          ))}
        </ul>
      )}
      {stretch && (
        <div className="stretch">
          <strong>{t("Worth stretching for")}</strong>
          <p className="hint nm">
            {stretch.stay.name} · {stretch.stay.stars}★ · {money(stretch.stay.group_price_per_night_inr || stretch.stay.price_per_night_inr)} {t("a night")}
            {" — "}{t("{n} more for the whole stay", { n: money(stretch.extra_total_inr) })}
          </p>
          <WhyLine why={stretch.stay.why} tr={tr} />
          <p className="hint">{t("Only if you can raise the budget — your plan stays inside the number you set.")}</p>
        </div>
      )}
    </details>
  );
}
