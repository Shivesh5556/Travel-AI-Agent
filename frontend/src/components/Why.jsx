import { useI18n } from "../i18n.jsx";

/*
  Small pieces that make a recommendation explain itself.

  Every stop and every stay arrives from the backend with a 0-100 score, a
  confidence level and a plain-English reason that was built in Python from the
  real evidence (not written by the language model). These components only show
  them.
*/

const LEVEL = {
  high: { cls: "good", label: "High confidence" },
  medium: { cls: "mid", label: "Medium confidence" },
  low: { cls: "warn", label: "Low confidence" },
};

export function ConfidenceChip({ confidence, score }) {
  const { t } = useI18n();
  const level = LEVEL[confidence];
  if (!level || score == null) return null;
  return (
    <span className={`tag conf ${level.cls}`} title={t(level.label)}>
      {score}/100 · {t(level.label)}
    </span>
  );
}

export function WhyLine({ why, tr }) {
  const { t } = useI18n();
  if (!why) return null;
  return (
    <p className="why-line">
      <strong>{t("Why Ghumi picked this")}:</strong> {tr ? tr(why) : why}
    </p>
  );
}
