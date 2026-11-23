import type { ClaimStatus, DecisionKind } from "./api";

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
