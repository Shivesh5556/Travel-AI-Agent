/*
  What the user sees WHILE the plan is being built.

  Deliberately not the raw agent trace. Somebody planning a holiday does not
  want to read "tool:search_transport:result" or a Groq rate-limit message -
  they want to know it is working and roughly how far along it is.

  So we map the agent's internal steps onto five plain-English phases. The
  real trace is still recorded and is available in the collapsed panel at the
  bottom of a finished plan.
*/

const PHASES = [
  { id: "look", label: "Looking up your route and the weather" },
  { id: "find", label: "Finding real places to visit" },
  { id: "money", label: "Making it fit your budget" },
  { id: "route", label: "Working out the best order for each day" },
  { id: "check", label: "Double-checking and fixing the plan" },
  { id: "write", label: "Writing it up" },
];

// Which phase does this internal step belong to?
function phaseOf(step) {
  if (step.includes("weather") || step.includes("transport") || step === "fill:start") return "look";
  if (step.includes("places") || step.includes("attractions") ||
      step.includes("restaurants") || step.includes("stays") ||
      step.includes("tips") || step === "fill:done") return "find";
  if (step.includes("budget") || step.includes("solve") || step.includes("replan")) return "money";
  if (step.includes("route") || step.includes("lunch")) return "route";
  if (step.includes("review")) return "check";
  if (step.includes("write") || step === "done") return "write";
  return null;
}

import { useI18n } from "../i18n.jsx";

export default function PlanningProgress({ events }) {
  const { t } = useI18n();
  const reached = new Set();
  for (const event of events) {
    const phase = phaseOf(event.step);
    if (phase) reached.add(phase);
  }

  // The current phase is the last one we have any sign of.
  const lastIndex = PHASES.reduce(
    (found, phase, i) => (reached.has(phase.id) ? i : found),
    -1
  );

  return (
    <div className="card">
      <h3 className="card-title">{t("Planning your trip")}</h3>
      <p className="hint" style={{ marginTop: "-8px", marginBottom: "20px" }}>
        {t("This takes up to a minute. It is checking live weather, real places and real fares, and it will re-plan if the first version does not fit your budget.")}
      </p>

      <ol className="phases">
        {PHASES.map((phase, i) => {
          const state =
            i < lastIndex ? "done" : i === lastIndex ? "active" : "waiting";
          return (
            <li key={phase.id} data-state={state}>
              <span className="dot" aria-hidden="true" />
              {t(phase.label)}
              {state === "active" && <span className="working" />}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
