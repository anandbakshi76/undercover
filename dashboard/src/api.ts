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
  grade: number | null;
  judge_cost: number;
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

export interface ClaimGrade {
  score: number;
  letter: "A" | "B" | "C" | "F";
  same_grounds: number;
  same_section: number;
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
  grade: ClaimGrade | null;
  judge_cost: number;
}

export interface EvaluationDetail extends Evaluation {
  claims: EvaluationClaim[];
}

export interface DiffLine {
  kind: "added" | "removed" | "same" | "gap";
  text: string;
}

export interface Setting {
  key: string;
  value: unknown;
  changed_by: string | null;
  changed_at: string | null;
}

export interface SettingChange {
  key: string;
  old: unknown;
  new: unknown;
  changed_by: string;
  changed_at: string;
}

export interface ModelAllowed {
  model: string;
  reasoning_efforts: string[];
  input_price: number;
  output_price: number;
}

export interface VersionScore {
  decision_accuracy: number;
  accuracy: number;
  grade: number | null;
  cost_per_claim: number;
  seconds_per_claim: number;
  runs: number;
}

export interface ConfigVersion {
  version: number;
  model: string;
  reasoning_effort: string;
  reason: string;
  proposed_by: string;
  approved_by: string;
  approved_at: string;
  round_id: string | null;
  is_current: boolean;
  scores: { evaluation: VersionScore | null; held_out: VersionScore | null };
}

export interface CurrentConfiguration {
  version: number;
  instructions: string;
  model: string;
  reasoning_effort: string;
  reason: string;
  proposed_by: string;
  approved_by: string;
  approved_at: string;
}

export interface Change<T> {
  before: T | null;
  after: T;
}

export interface ConfigDiff extends Omit<
  ConfigVersion,
  "scores" | "model" | "reasoning_effort"
> {
  previous: number | null;
  instructions: DiffLine[];
  model: Change<string>;
  reasoning_effort: Change<string>;
}

export type RoundStage =
  | "first_evaluations"
  | "improver_working"
  | "change_waiting"
  | "evaluating"
  | "verdict"
  | "recipe_waiting"
  | "finished"
  | "stopped";

export interface RoundScores {
  decision_accuracy: number;
  accuracy: number;
  grade: number | null;
  cost_per_claim: number;
  seconds_per_claim: number;
  runs: number;
}

export interface Proposal {
  id: number;
  kind: "change" | "recipe";
  reason: string;
  status: "pending" | "approved" | "rejected" | "withdrawn";
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
  rejection_reason: string | null;
  model?: Change<string>;
  reasoning_effort?: Change<string>;
  diff: DiffLine[];
}

export interface Round {
  round_id: string;
  status: "working" | "waiting" | "finished" | "stopped";
  stage: RoundStage;
  progress: number;
  recipe_version: number;
  start_version: number;
  version_tried: number | null;
  reason: string | null;
  before: RoundScores | null;
  after: RoundScores | null;
  verdict: "kept" | "rolled_back" | null;
  why: string | null;
  started_by: string | null;
  started_at: string;
  finished_at: string | null;
  spent: number;
  limit: number;
  proposals: Proposal[];
  pending: Proposal | null;
}

export interface RecipeVersion {
  version: number;
  reason: string;
  proposed_by: string;
  approved_by: string;
  approved_at: string;
  round_id: string | null;
  is_current: boolean;
}

export interface RecipeDiff {
  version: number;
  previous: number | null;
  reason: string;
  proposed_by: string;
  approved_by: string;
  approved_at: string;
  lines: DiffLine[];
}

export interface Handler {
  handler_id: string;
  name: string;
  role: string;
  email: string;
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

let accessToken: string | null = null;
let whenUnauthorized: () => void = () => undefined;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function setUnauthorizedHandler(handler: () => void): void {
  whenUnauthorized = handler;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const response = await fetch(path, { ...init, headers });
  const body: unknown = await response.json();
  if (response.status === 401 && accessToken) whenUnauthorized();
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
  settings: () => request<Setting[]>("/api/settings"),
  settingChanges: () => request<SettingChange[]>("/api/settings/changes"),
  changeSetting: (key: string, value: unknown) =>
    request<Setting>(`/api/settings/${key}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    }),
  models: () => request<ModelAllowed[]>("/api/models"),
  configurations: () => request<ConfigVersion[]>("/api/configurations"),
  currentConfiguration: () =>
    request<CurrentConfiguration>("/api/configurations/current"),
  configurationDiff: (version: number) =>
    request<ConfigDiff>(`/api/configurations/${String(version)}/diff`),
  rounds: () => request<Round[]>("/api/rounds"),
  startRound: () => post<Round>("/api/rounds"),
  stopRound: (roundId: string) => post<Round>(`/api/rounds/${roundId}/stop`),
  approveProposal: (id: number) =>
    post<Round>(`/api/proposals/${String(id)}/approve`),
  rejectProposal: (id: number, reason: string) =>
    post<Round>(`/api/proposals/${String(id)}/reject`, { reason }),
  recipes: () => request<RecipeVersion[]>("/api/recipes"),
  recipeDiff: (version: number) =>
    request<RecipeDiff>(`/api/recipes/${String(version)}/diff`),
  latestDispatch: () => request<Run | null>("/api/dispatches/latest"),
  me: () => request<Handler>("/api/me"),
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
  signOff: (claimId: string) =>
    post<ClaimDetail>(`/api/claims/${claimId}/sign-off`),
};
