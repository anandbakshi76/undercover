import type {
  ClaimStatus,
  DecisionKind,
  EvaluationSet,
  RunStatus,
} from "./api";

const dollars = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

export function money(amount: number): string {
  return dollars.format(amount);
}

const times = new Intl.DateTimeFormat("en-US", {
  dateStyle: "medium",
  timeStyle: "short",
});

export function dateTime(iso: string): string {
  return times.format(new Date(iso));
}

export function words(value: string): string {
  const spaced = value.replaceAll("_", " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export const STATUS_LABELS: Record<ClaimStatus, string> = {
  waiting: "Waiting",
  in_progress: "In progress",
  awaiting_sign_off: "Waiting for sign-off",
  decided: "Decided",
  needs_handler: "Needs a handler",
};

export const STATUSES = Object.keys(STATUS_LABELS) as ClaimStatus[];

export const DECISION_LABELS: Record<DecisionKind, string> = {
  approve: "Approved",
  decline: "Declined",
  refer: "Referred",
};

export const RUN_LABELS: Record<RunStatus, string> = {
  running: "Running",
  finished: "Finished",
  failed: "Failed",
  stopped: "Stopped",
};

export function cost(amount: number): string {
  return `$${amount.toFixed(4)}`;
}

export function duration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes)} min ${String(Math.round(seconds - minutes * 60))} s`;
}

export function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export const SET_LABELS: Record<EvaluationSet, string> = {
  evaluation: "Evaluation set",
  held_out: "Held-out set",
};

export function percent(share: number): string {
  return `${(share * 100).toFixed(0)}%`;
}

export function yesNo(probability: number): string {
  return probability >= 0.5 ? "Yes" : "No";
}

export function gradeText(score: number | null): string {
  return score === null ? "Not graded" : score.toFixed(2);
}
