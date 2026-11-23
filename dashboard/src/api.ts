export type ClaimStatus =
  "waiting" | "in_progress" | "awaiting_sign_off" | "decided" | "needs_handler";

export type DecisionKind = "approve" | "decline" | "refer";

interface ClaimBase {
  claim_id: string;
  claim_type: string;
  summary: string;
  amount_claimed: number;
  submitted_on: string;
  status: ClaimStatus;
}

export interface ClaimSummary extends ClaimBase {
  decision: DecisionKind | null;
  payout: number | null;
}

export interface Item {
  description: string;
  category: string;
  amount: number;
}

export interface ClaimDocument {
  name: string;
  type: string;
  content: string;
}

export interface Decision {
  decision: DecisionKind;
  payout: number;
  reason: string;
  policy_section: string;
  decided_at: string;
}

export interface ClaimDetail extends ClaimBase {
  policy_id: string;
  customer_id: string;
  incident_date: string;
  incident_location: string;
  story: string;
  items: Item[];
  documents: ClaimDocument[];
  decision: Decision | null;
  sign_off: { handler_id: string; signed_off_at: string } | null;
  payment: {
    amount: number;
    paid_at: string;
    signed_off_by: string | null;
    signed_off_at: string | null;
  } | null;
  attempts: number;
  thread_id: string | null;
  run_id: string | null;
  evaluation_id: string | null;
  source_id: string | null;
}

export interface Policy {
  policy_id: string;
  plan: string;
  purchased_on: string;
  trip: {
    destination: string;
    departs_on: string;
    returns_on: string;
    trip_cost: number;
  };
  travelers: string[];
  plan_details: { name: string };
  add_on_details: { add_on: string; name: string }[];
}

export interface Step {
  id: number;
  kind: "tool_call" | "tool_result" | "section" | "reply" | "error";
  text: string;
  created_at: string;
}

export type RunStatus = "running" | "finished" | "failed" | "stopped";

export interface RunClaim {
  claim_id: string;
  status: ClaimStatus;
  decision: DecisionKind | null;
  payout: number | null;
  started_at: string | null;
  seconds: number;
  cost: number;
}

export interface RunSummary {
  approved: number;
  declined: number;
  referred: number;
  awaiting_sign_off: { claim_id: string; payout: number }[];
  needs_handler: string[];
  in_progress: string[];
}

export interface Run {
  run_id: string;
  kind: "dispatch" | "evaluation" | "claim";
  concurrency: number;
  status: RunStatus;
  summary: RunSummary | null;
  started_at: string;
  finished_at: string | null;
  model: string;
  reasoning_effort: string;
  claims: RunClaim[];
  cost: number;
  work_seconds: number;
  elapsed_seconds: number;
}

export type EvaluationSet = "evaluation" | "held_out";

export interface Measures {
  claim_count: number;
  decision_accuracy: number;
  accuracy: number;
  cost_of_mistakes: number;
  straight_through: number;
  cost_per_claim: number;
  seconds_per_claim: number;
  run_seconds: number;
}

export interface Evaluation extends Measures {
  run_id: string;
  evaluation_set: EvaluationSet;
  tiny: boolean;
  concurrency: number;
  model: string;
  reasoning_effort: string;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
}

export interface Answer {
  decision: DecisionKind;
  payout: number;
}

export interface EvaluationClaim {
  claim_id: string;
  source_id: string;
  status: ClaimStatus;
  handler: Answer;
  agent: Answer;
  reason: string | null;
  right: boolean;
  mistake_cost: number;
  cost: number;
  seconds: number;
  started_at: string | null;
}

export interface EvaluationDetail extends Evaluation {
  claims: EvaluationClaim[];
}

export interface Instructions {
  claim: string;
}

export interface Handler {
  handler_id: string;
  name: string;
  role: string;
  can_approve_over_limit: boolean;
}

function detail(body: unknown): string {
  if (
    typeof body === "object" &&
    body !== null &&
    "detail" in body &&
    typeof body.detail === "string"
  ) {
    return body.detail;
  }
  return "Something went wrong. Please try again.";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  const body: unknown = await response.json();
  if (!response.ok) throw new Error(detail(body));
  return body as T;
}

function post<T>(path: string, data?: object): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
}

export const api = {
  claims: () => request<ClaimSummary[]>("/api/claims"),
  claim: (claimId: string) => request<ClaimDetail>(`/api/claims/${claimId}`),
  policy: (policyId: string) => request<Policy>(`/api/policies/${policyId}`),
  steps: (claimId: string) => request<Step[]>(`/api/claims/${claimId}/steps`),
  instructions: () => request<Instructions>("/api/instructions"),
  latestDispatch: () => request<Run | null>("/api/dispatches/latest"),
  handlers: () => request<Handler[]>("/api/handlers"),
  evaluations: () => request<Evaluation[]>("/api/evaluations"),
  evaluation: (runId: string) =>
    request<EvaluationDetail>(`/api/evaluations/${runId}`),
  startEvaluation: (
    concurrency: number,
    evaluationSet: EvaluationSet,
    tiny: boolean,
  ) =>
    post<Evaluation>("/api/evaluations", {
      concurrency,
      evaluation_set: evaluationSet,
      tiny,
    }),
  startDispatch: (concurrency: number) =>
    post<Run>("/api/dispatches", { concurrency }),
  runAgent: (claimId: string) =>
    post<{ status: string }>(`/api/claims/${claimId}/run`),
  signOff: (claimId: string, handlerId: string) =>
    post<ClaimDetail>(`/api/claims/${claimId}/sign-off`, {
      handler_id: handlerId,
    }),
};
