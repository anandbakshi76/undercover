import { vi } from "vitest";
import type {
  ConfigDiff,
  ConfigVersion,
  CurrentConfiguration,
  ModelAllowed,
  Setting,
  SettingChange,
  ClaimDetail,
  ClaimSummary,
  Handler,
  Evaluation,
  EvaluationClaim,
  EvaluationDetail,
  Policy,
  Run,
  RunClaim,
  Step,
  Proposal,
  RecipeVersion,
  Round,
} from "./api";

export class Failure {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {}
}

export class Replies {
  private next = 0;

  constructor(private readonly replies: unknown[]) {}

  take(): unknown {
    const reply = this.replies[Math.min(this.next, this.replies.length - 1)];
    this.next += 1;
    return reply;
  }
}

function respond(reply: unknown, key: string): Response {
  if (reply === undefined) {
    return new Response(JSON.stringify({ detail: `No mock for ${key}` }), {
      status: 404,
    });
  }
  if (reply instanceof Failure) {
    return new Response(JSON.stringify({ detail: reply.detail }), {
      status: reply.status,
    });
  }
  return new Response(JSON.stringify(reply));
}

export function mockApi(routes: Record<string, unknown>) {
  const fetchMock = vi.fn((input: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${input}`;
    const route = routes[key];
    const reply = route instanceof Replies ? route.take() : route;
    return Promise.resolve(respond(reply, key));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export function calls(fetchMock: ReturnType<typeof mockApi>): string[] {
  return fetchMock.mock.calls.map(
    ([input, init]) => `${init?.method ?? "GET"} ${input}`,
  );
}

export function summary(overrides: Partial<ClaimSummary> = {}): ClaimSummary {
  return {
    claim_id: "CLM-1001",
    claim_type: "trip_delay",
    summary: "Flight home canceled by the airline",
    amount_claimed: 420,
    submitted_on: "2026-06-24",
    status: "waiting",
    decision: null,
    payout: null,
    ...overrides,
  };
}

export function claim(overrides: Partial<ClaimDetail> = {}): ClaimDetail {
  return {
    claim_id: "CLM-1001",
    claim_type: "trip_delay",
    summary: "Flight home canceled by the airline",
    amount_claimed: 420,
    submitted_on: "2026-06-24",
    status: "waiting",
    policy_id: "POL-2001",
    customer_id: "CUS-3001",
    incident_date: "2026-06-18",
    incident_location: "Rome, Italy",
    story: "My flight home from Rome was canceled.",
    items: [
      {
        description: "Airport hotel, one night",
        category: "hotel",
        amount: 245,
      },
    ],
    documents: [
      {
        name: "Hotel receipt",
        type: "receipt",
        content: "Hotel Aurelia. Total paid: $245.00.",
      },
    ],
    decision: null,
    sign_off: null,
    payment: null,
    attempts: 0,
    thread_id: null,
    run_id: null,
    evaluation_id: null,
    source_id: null,
    ...overrides,
  };
}

export const policy: Policy = {
  policy_id: "POL-2001",
  plan: "premium",
  purchased_on: "2026-04-22",
  trip: {
    destination: "Rome, Italy",
    departs_on: "2026-06-10",
    returns_on: "2026-06-18",
    trip_cost: 3100,
  },
  travelers: ["Maya Patel"],
  plan_details: { name: "UnderCover Premium" },
  add_on_details: [{ add_on: "electronics_plus", name: "Electronics Plus" }],
};

export const dana: Handler = {
  handler_id: "HND-01",
  name: "Dana Whitfield",
  role: "Senior Claims Handler",
  email: "dana.whitfield@undercover.example",
  can_approve_over_limit: true,
};

export const marcus: Handler = {
  handler_id: "HND-02",
  name: "Marcus Oyelaran",
  role: "Claims Handler",
  email: "marcus.oyelaran@undercover.example",
  can_approve_over_limit: false,
};

export const handlers: Handler[] = [dana, marcus];

export function step(id: number, overrides: Partial<Step> = {}): Step {
  return {
    id,
    kind: "tool_call",
    text: `get_claim({"claim_id": "CLM-1001"}) ${String(id)}`,
    created_at: "2026-10-28T10:00:00+00:00",
    ...overrides,
  };
}

export function runClaim(overrides: Partial<RunClaim> = {}): RunClaim {
  return {
    claim_id: "CLM-1001",
    status: "decided",
    decision: "approve",
    payout: 420,
    started_at: "2026-11-08T10:00:01.000+00:00",
    seconds: 12.5,
    cost: 0.0123,
    ...overrides,
  };
}

export function run(overrides: Partial<Run> = {}): Run {
  return {
    run_id: "RUN-0001",
    kind: "dispatch",
    concurrency: 3,
    status: "running",
    summary: null,
    started_at: "2026-11-08T10:00:00.000+00:00",
    finished_at: null,
    model: "openai/gpt-6-luna",
    reasoning_effort: "low",
    claims: [],
    cost: 0,
    work_seconds: 0,
    elapsed_seconds: 3,
    ...overrides,
  };
}

export function evaluation(overrides: Partial<Evaluation> = {}): Evaluation {
  return {
    run_id: "EVAL-0001",
    evaluation_set: "evaluation",
    tiny: false,
    concurrency: 25,
    model: "openai/gpt-6-luna",
    reasoning_effort: "low",
    status: "finished",
    started_at: "2026-11-08T10:00:00.000+00:00",
    finished_at: "2026-11-08T10:01:00.000+00:00",
    claim_count: 25,
    decision_accuracy: 0.84,
    accuracy: 0.76,
    cost_of_mistakes: 3250,
    straight_through: 0.8,
    cost_per_claim: 0.0123,
    seconds_per_claim: 31.4,
    run_seconds: 83,
    grade: 1.4,
    judge_cost: 0.0112,
    ...overrides,
  };
}

export function evaluationClaim(
  overrides: Partial<EvaluationClaim> = {},
): EvaluationClaim {
  return {
    claim_id: "EVAL-0001-CLM-1001",
    source_id: "CLM-1001",
    status: "decided",
    handler: { decision: "approve", payout: 420 },
    agent: { decision: "approve", payout: 420 },
    reason: "Covered.",
    right: true,
    mistake_cost: 0,
    cost: 0.0123,
    seconds: 12.5,
    started_at: "2026-11-08T10:00:01.000+00:00",
    grade: { score: 2.1, letter: "B", same_grounds: 0.9, same_section: 0.1 },
    judge_cost: 0.0004,
    ...overrides,
  };
}

export function evaluationDetail(
  overrides: Partial<EvaluationDetail> = {},
): EvaluationDetail {
  return { ...evaluation(), claims: [evaluationClaim()], ...overrides };
}

export function version(overrides: Partial<ConfigVersion> = {}): ConfigVersion {
  return {
    version: 1,
    model: "openai/gpt-6-luna",
    reasoning_effort: "low",
    reason: "The claim agent as it was before the improver.",
    proposed_by: "UnderCover",
    approved_by: "UnderCover",
    approved_at: "2026-11-25T10:00:00+00:00",
    round_id: null,
    is_current: true,
    scores: { evaluation: null, held_out: null },
    ...overrides,
  };
}

export function currentConfiguration(
  overrides: Partial<CurrentConfiguration> = {},
): CurrentConfiguration {
  return {
    version: 1,
    instructions: "# Working a claim\n\nDecide one claim.",
    model: "openai/gpt-6-luna",
    reasoning_effort: "low",
    reason: "The claim agent as it was before the improver.",
    proposed_by: "UnderCover",
    approved_by: "UnderCover",
    approved_at: "2026-11-25T10:00:00+00:00",
    ...overrides,
  };
}

export function configDiff(overrides: Partial<ConfigDiff> = {}): ConfigDiff {
  return {
    version: 1,
    model: { before: null, after: "openai/gpt-6-luna" },
    reasoning_effort: { before: null, after: "low" },
    reason: "The claim agent as it was before the improver.",
    proposed_by: "UnderCover",
    approved_by: "UnderCover",
    approved_at: "2026-11-25T10:00:00+00:00",
    round_id: null,
    is_current: true,
    previous: null,
    instructions: [],
    ...overrides,
  };
}

export function setting(
  key: string,
  value: unknown,
  overrides: Partial<Setting> = {},
): Setting {
  return { key, value, changed_by: null, changed_at: null, ...overrides };
}

export const STARTING_SETTINGS: Setting[] = [
  setting("approval_limit", 1000),
  setting("objectives", { accuracy: 0.6, grade: 1 }),
  setting("noise", {
    accuracy: 0.1,
    grade: 0.15,
    cost_per_claim: 0.15,
    seconds_per_claim: 0.15,
  }),
  setting("tolerance", {
    accuracy: 0.1,
    grade: 0.15,
    cost_per_claim: 1,
    seconds_per_claim: 0.25,
  }),
  setting("round_limit", 0.5),
  setting("improver_concurrency", 25),
];

export const MODELS: ModelAllowed[] = [
  {
    model: "openai/gpt-6-luna",
    reasoning_efforts: ["none", "low", "medium"],
    input_price: 0.1,
    output_price: 0.5,
  },
];

export function settingChange(
  overrides: Partial<SettingChange> = {},
): SettingChange {
  return {
    key: "round_limit",
    old: 0.5,
    new: 0.75,
    changed_by: "Dana Whitfield",
    changed_at: "2026-11-25T11:00:00+00:00",
    ...overrides,
  };
}

export function round(overrides: Partial<Round> = {}): Round {
  return {
    round_id: "ROUND-1",
    status: "working",
    stage: "improver_working",
    progress: 0,
    recipe_version: 1,
    start_version: 1,
    version_tried: null,
    reason: null,
    before: null,
    after: null,
    verdict: null,
    why: null,
    started_by: "Dana Whitfield",
    started_at: "2026-11-25T10:00:00+00:00",
    finished_at: null,
    spent: 0.1,
    limit: 0.5,
    proposals: [],
    pending: null,
    ...overrides,
  };
}

export function proposal(overrides: Partial<Proposal> = {}): Proposal {
  return {
    id: 1,
    kind: "change",
    reason: "Shorter answers are cheaper.",
    status: "pending",
    created_at: "2026-11-25T10:05:00+00:00",
    decided_by: null,
    decided_at: null,
    rejection_reason: null,
    model: { before: "openai/gpt-6-luna", after: "openai/gpt-6-luna" },
    reasoning_effort: { before: "low", after: "medium" },
    diff: [
      { kind: "removed", text: "Be thorough." },
      { kind: "added", text: "Be brief." },
    ],
    ...overrides,
  };
}

export const RECIPES: RecipeVersion[] = [
  {
    version: 1,
    reason: "The lab's first recipe.",
    proposed_by: "UnderCover",
    approved_by: "UnderCover",
    approved_at: "2026-11-25T10:00:00+00:00",
    round_id: null,
    is_current: true,
  },
];
