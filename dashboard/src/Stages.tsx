import type { Round, RoundStage } from "./api";

const STAGES: { stage: RoundStage; label: string; counted: boolean }[] = [
  { stage: "first_evaluations", label: "First evaluations", counted: true },
  { stage: "improver_working", label: "Improver working", counted: false },
  { stage: "change_waiting", label: "Change waiting", counted: false },
  { stage: "evaluating", label: "Evaluations", counted: true },
  { stage: "verdict", label: "Verdict", counted: false },
  { stage: "recipe_waiting", label: "Recipe waiting", counted: false },
  { stage: "finished", label: "Finished", counted: false },
];

const TOTAL = 3;

function stageState(index: number, here: number) {
  if (index < here) return "done";
  return index === here ? "now" : "next";
}

export function Stages({ round }: Readonly<{ round: Round }>) {
  const here =
    round.stage === "stopped"
      ? -1
      : STAGES.findIndex((s) => s.stage === round.stage);
  return (
    <ol className="stages" aria-label="Where the round has got to">
      {STAGES.map((item, index) => {
        const state = stageState(index, here);
        const count =
          item.counted && state === "now"
            ? ` (${String(round.progress + 1)} of ${String(TOTAL)})`
            : "";
        return (
          <li
            key={item.stage}
            className={`stage stage-${state}`}
            aria-current={state === "now" ? "step" : undefined}
          >
            {item.label}
            {count}
          </li>
        );
      })}
      {round.stage === "stopped" && (
        <li className="stage stage-now">Stopped</li>
      )}
    </ol>
  );
}
