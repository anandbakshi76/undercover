import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EvaluationPage } from "./EvaluationPage";
import { POLL_MS } from "./hooks";
import { Scoreboard } from "./Scoreboard";
import {
  confirmDialog,
  calls,
  evaluation,
  evaluationClaim,
  evaluationDetail,
  Failure,
  mockApi,
  Replies,
} from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const newest = evaluation({
  run_id: "EVAL-0002",
  evaluation_set: "held_out",
  tiny: true,
  concurrency: 5,
  claim_count: 5,
  model: "openrouter/free",
  reasoning_effort: "none",
  decision_accuracy: 1,
  accuracy: 0.8,
  cost_of_mistakes: 0,
  straight_through: 1,
  cost_per_claim: 0,
  seconds_per_claim: 4,
  run_seconds: 61,
});
const older = evaluation();
const stopped = evaluation({ run_id: "EVAL-0000", status: "stopped" });

function rowOf(runId: string): HTMLElement {
  const table = screen.getByRole("table", { name: "Evaluations" });
  const link = within(table)
    .getAllByRole("link")
    .find((item) => item.getAttribute("href") === `#/evaluations/${runId}`);
  const row = link?.closest("tr");
  if (!row) throw new Error(`No row for ${runId}`);
  return row;
}

it("lists the evaluations newest first with every measure", async () => {
  mockApi({ "GET /api/evaluations": [newest, older, stopped] });
  render(<Scoreboard />);
  await screen.findByRole("table", { name: "Evaluations" });
  const rows = screen.getAllByRole("row").slice(1);
  expect(rows).toHaveLength(3);
  const first = within(rowOf("EVAL-0002"));
  expect(first.getByText("Held-out set")).toBeDefined();
  expect(first.getByText("openrouter/free")).toBeDefined();
  expect(first.getByText("reasoning none", { exact: false })).toBeDefined();
  expect(first.getByText("80%")).toBeDefined();
  expect(first.getByText("$0.0000")).toBeDefined();
  expect(first.getByText("4.0 s")).toBeDefined();
  expect(first.getByText("1 min 1 s")).toBeDefined();
  const second = within(rowOf("EVAL-0001"));
  expect(second.getByText("Evaluation set")).toBeDefined();
  expect(second.getByText("84%")).toBeDefined();
  expect(second.getByText("76%")).toBeDefined();
  expect(second.getByText("$3,250.00")).toBeDefined();
  expect(second.getByText("$0.0123")).toBeDefined();
  expect(second.getByText("31.4 s")).toBeDefined();
  expect(second.getByText("1 min 23 s")).toBeDefined();
  expect(within(rowOf("EVAL-0000")).getByText("Stopped")).toBeDefined();
  expect(within(rows[0]).getAllByRole("link")[0].getAttribute("href")).toBe(
    "#/evaluations/EVAL-0002",
  );
});

it("says when there are no evaluations yet", async () => {
  mockApi({ "GET /api/evaluations": [] });
  render(<Scoreboard />);
  expect(
    await screen.findByText("There have been no evaluations yet."),
  ).toBeDefined();
});

it("starts an evaluation with the set, tiny and N from the form", async () => {
  const fetchMock = mockApi({
    "GET /api/evaluations": [older],
    "POST /api/evaluations": evaluation(),
  });
  render(<Scoreboard />);
  expect(
    (await screen.findByLabelText("Claim agents at once")).getAttribute(
      "value",
    ),
  ).toBe("3");
  fireEvent.change(screen.getByLabelText("Set"), {
    target: { value: "held_out" },
  });
  fireEvent.change(screen.getByLabelText("Claims"), {
    target: { value: "tiny" },
  });
  fireEvent.change(screen.getByLabelText("Claim agents at once"), {
    target: { value: "5" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Start an evaluation" }));
  confirmDialog("Start the evaluation");
  await vi.waitFor(() => {
    expect(calls(fetchMock)).toContain("POST /api/evaluations");
  });
  const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
  expect(post?.[1]?.body).toBe(
    JSON.stringify({ concurrency: 5, evaluation_set: "held_out", tiny: true }),
  );
});

it("starts the whole evaluation set at 3 by default", async () => {
  const fetchMock = mockApi({
    "GET /api/evaluations": [],
    "POST /api/evaluations": evaluation(),
  });
  render(<Scoreboard />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Start an evaluation" }),
  );
  confirmDialog("Start the evaluation");
  await vi.waitFor(() => {
    const post = fetchMock.mock.calls.find(
      ([, init]) => init?.method === "POST",
    );
    expect(post?.[1]?.body).toBe(
      JSON.stringify({
        concurrency: 3,
        evaluation_set: "evaluation",
        tiny: false,
      }),
    );
  });
});

it("limits N to 25 and cannot start while an evaluation is running", async () => {
  mockApi({ "GET /api/evaluations": [evaluation({ status: "running" })] });
  render(<Scoreboard />);
  const button = await screen.findByRole("button", {
    name: "Start an evaluation",
  });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  cleanup();
  mockApi({ "GET /api/evaluations": [] });
  render(<Scoreboard />);
  const enabled = await screen.findByRole("button", {
    name: "Start an evaluation",
  });
  fireEvent.change(screen.getByLabelText("Claim agents at once"), {
    target: { value: "26" },
  });
  expect((enabled as HTMLButtonElement).disabled).toBe(true);
});

it("shows a refusal, such as the service not running", async () => {
  mockApi({
    "GET /api/evaluations": [],
    "POST /api/evaluations": new Failure(503, "The agent isn't running."),
  });
  render(<Scoreboard />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Start an evaluation" }),
  );
  confirmDialog("Start the evaluation");
  expect((await screen.findByRole("alert")).textContent).toBe(
    "The agent isn't running.",
  );
});

it("refreshes while an evaluation is running", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  mockApi({
    "GET /api/evaluations": new Replies([
      [evaluation({ status: "running", accuracy: 0.1 })],
      [evaluation({ accuracy: 0.9 })],
    ]),
  });
  render(<Scoreboard />);
  expect(await screen.findByText("10%")).toBeDefined();
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 2));
  expect(screen.getByText("90%")).toBeDefined();
});

it("shows an error when the scoreboard cannot load", async () => {
  mockApi({ "GET /api/evaluations": new Failure(500, "The backend is down.") });
  render(<Scoreboard />);
  expect((await screen.findByRole("alert")).textContent).toContain(
    "The backend is down.",
  );
});

const detail = evaluationDetail({
  claims: [
    evaluationClaim(),
    evaluationClaim({
      claim_id: "EVAL-0001-CLM-1002",
      source_id: "CLM-1002",
      status: "awaiting_sign_off",
      handler: { decision: "decline", payout: 0 },
      agent: { decision: "approve", payout: 1250 },
      right: false,
      mistake_cost: 1250,
    }),
    evaluationClaim({
      claim_id: "EVAL-0001-CLM-1003",
      source_id: "CLM-1003",
      status: "needs_handler",
      handler: { decision: "approve", payout: 300 },
      agent: { decision: "refer", payout: 0 },
      right: false,
      mistake_cost: 50,
      started_at: null,
    }),
  ],
});

it("opens to the claims with the handler's answer beside the agent's", async () => {
  mockApi({ "GET /api/evaluations/EVAL-0001": detail });
  render(<EvaluationPage runId="EVAL-0001" />);
  const measures = await screen.findByLabelText("Measures");
  expect(within(measures).getByText("84%")).toBeDefined();
  expect(within(measures).getByText("$3,250.00")).toBeDefined();
  expect(within(measures).getByText("1 min 23 s")).toBeDefined();
  const rows = within(
    screen.getByRole("table", { name: "Evaluation claims" }),
  ).getAllByRole("row");
  expect(rows).toHaveLength(4);
  const hit = within(rows[1]);
  expect(hit.getByText("Match")).toBeDefined();
  const miss = within(rows[2]);
  expect(miss.getByText("Miss")).toBeDefined();
  expect(miss.getByText("Declined")).toBeDefined();
  expect(miss.getByText("Approved")).toBeDefined();
  expect(miss.getAllByText("$1,250.00")).toHaveLength(2);
  expect(rows[2].className).toBe("miss");
  expect(rows[1].className).toBe("");
  expect(within(rows[3]).getByText("Referred")).toBeDefined();
});

it("links each claim to its copy's page", async () => {
  mockApi({ "GET /api/evaluations/EVAL-0001": detail });
  render(<EvaluationPage runId="EVAL-0001" />);
  const link = await screen.findByRole("link", { name: "CLM-1002" });
  expect(link.getAttribute("href")).toBe("#/claims/EVAL-0001-CLM-1002");
  expect(
    screen.getByRole("link", { name: "Back to the scoreboard" }),
  ).toBeDefined();
});

it("refreshes a running evaluation and shows a stopped one as stopped", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  mockApi({
    "GET /api/evaluations/EVAL-0001": new Replies([
      evaluationDetail({ status: "running", claims: [] }),
      evaluationDetail({ status: "stopped", tiny: true }),
    ]),
  });
  render(<EvaluationPage runId="EVAL-0001" />);
  expect(await screen.findByText("Running")).toBeDefined();
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 2));
  expect(screen.getByText("Stopped")).toBeDefined();
  expect(screen.getByText("Evaluation set, tiny")).toBeDefined();
});

it("shows an error when the evaluation cannot load", async () => {
  mockApi({
    "GET /api/evaluations/EVAL-9": new Failure(
      404,
      "There is no evaluation EVAL-9.",
    ),
  });
  render(<EvaluationPage runId="EVAL-9" />);
  expect((await screen.findByRole("alert")).textContent).toContain(
    "There is no evaluation EVAL-9.",
  );
});

it("shows the average grade beside the other measures, and says when there is none", async () => {
  mockApi({
    "GET /api/evaluations": [
      evaluation({ run_id: "EVAL-0003", grade: 1.4 }),
      evaluation({ run_id: "EVAL-0002", grade: null }),
    ],
  });
  render(<Scoreboard />);
  const table = await screen.findByRole("table", { name: "Evaluations" });
  expect(
    within(table).getByRole("columnheader", { name: "Grade" }),
  ).toBeDefined();
  const cells = within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) => row.textContent);
  expect(cells[0]).toContain("1.40");
  expect(cells[1]).toContain("Not graded");
});

it("shows each claim's grade with its two checks as yes or no", async () => {
  mockApi({
    "GET /api/evaluations/EVAL-0001": evaluationDetail({
      claims: [
        evaluationClaim(),
        evaluationClaim({
          claim_id: "EVAL-0001-CLM-1002",
          source_id: "CLM-1002",
          grade: {
            score: 0.3,
            letter: "F",
            same_grounds: 0.2,
            same_section: 0.8,
          },
        }),
        evaluationClaim({
          claim_id: "EVAL-0001-CLM-1003",
          source_id: "CLM-1003",
          grade: null,
        }),
      ],
    }),
  });
  render(<EvaluationPage runId="EVAL-0001" />);
  const measures = await screen.findByLabelText("Measures");
  expect(within(measures).getByText("1.40")).toBeDefined();
  expect(within(measures).getByText("$0.0112")).toBeDefined();
  const rows = within(
    screen.getByRole("table", { name: "Evaluation claims" }),
  ).getAllByRole("row");
  const good = within(rows[1]);
  expect(good.getByText("B")).toBeDefined();
  expect(good.getByText("2.10")).toBeDefined();
  expect(good.getByText("Yes")).toBeDefined();
  expect(good.getByText("No")).toBeDefined();
  const bad = within(rows[2]);
  expect(bad.getByText("F")).toBeDefined();
  expect(bad.getByText("0.30")).toBeDefined();
  expect(bad.getByText("No", { selector: "td" })).toBeDefined();
  expect(bad.getByText("Yes")).toBeDefined();
  expect(within(rows[3]).getByText("Not graded")).toBeDefined();
});

it("opens a confirmation before an evaluation and calls nothing on Cancel", async () => {
  const fetchMock = mockApi({
    "GET /api/evaluations": [],
    "POST /api/evaluations": evaluation(),
  });
  render(<Scoreboard />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Start an evaluation" }),
  );
  expect(calls(fetchMock)).not.toContain("POST /api/evaluations");
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }),
  );
  expect(calls(fetchMock)).not.toContain("POST /api/evaluations");
});
