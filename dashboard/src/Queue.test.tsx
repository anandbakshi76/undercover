import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { POLL_MS } from "./hooks";
import { Queue } from "./Queue";
import {
  confirmDialog,
  calls,
  Failure,
  mockApi,
  Replies,
  run,
  runClaim,
  summary,
} from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const claims = [
  summary(),
  summary({
    claim_id: "CLM-1002",
    claim_type: "baggage",
    amount_claimed: 1150,
    status: "awaiting_sign_off",
    decision: "approve",
    payout: 1250,
  }),
  summary({ claim_id: "CLM-1003", status: "decided", decision: "decline" }),
  summary({ claim_id: "CLM-1004", status: "needs_handler" }),
];

const finishedRun = run({
  status: "finished",
  concurrency: 25,
  cost: 0.2468,
  elapsed_seconds: 83,
  claims: [
    runClaim(),
    runClaim({
      claim_id: "CLM-1002",
      status: "awaiting_sign_off",
      payout: 1250,
      seconds: 20,
      cost: 0.0345,
    }),
    runClaim({
      claim_id: "CLM-1004",
      status: "needs_handler",
      decision: null,
      payout: null,
    }),
  ],
  summary: {
    approved: 2,
    declined: 0,
    referred: 0,
    awaiting_sign_off: [{ claim_id: "CLM-1002", payout: 1250 }],
    needs_handler: ["CLM-1004"],
    in_progress: ["CLM-1005"],
  },
});

function routes(extra: Record<string, unknown> = {}) {
  return {
    "GET /api/claims": claims,
    "GET /api/dispatches/latest": null,
    ...extra,
  };
}

function count(label: string): string | null {
  const counts = screen.getByRole("list", { name: "Claims by state" });
  const item = within(counts).getByText(label).closest("li");
  return item?.querySelector(".count-number")?.textContent ?? null;
}

it("lists the claims with their states and amounts", async () => {
  mockApi(routes());
  render(<Queue />);
  const link = await screen.findByRole("link", { name: "CLM-1001" });
  expect(link.getAttribute("href")).toBe("#/claims/CLM-1001");
  expect(screen.getAllByText("$420.00")).toHaveLength(3);
  expect(screen.getByText("$1,150.00")).toBeDefined();
  expect(screen.getAllByText("Trip delay")).toHaveLength(3);
  expect(screen.getByText("$1,250.00")).toBeDefined();
  const table = screen.getByRole("table");
  expect(within(table).getByText("Waiting for sign-off")).toBeDefined();
  expect(within(table).getByText("Declined")).toBeDefined();
  expect(within(table).getByText("Needs a handler")).toBeDefined();
});

it("counts the claims in each state, including those left for a handler", async () => {
  mockApi(routes());
  render(<Queue />);
  await screen.findByRole("table");
  expect(count("Waiting")).toBe("1");
  expect(count("In progress")).toBe("0");
  expect(count("Waiting for sign-off")).toBe("1");
  expect(count("Decided")).toBe("1");
  expect(count("Needs a handler")).toBe("1");
});

it("starts with 3 claim agents at once, from 1 to 25", async () => {
  mockApi(routes());
  render(<Queue />);
  const box = await screen.findByLabelText("Claim agents at once");
  expect((box as HTMLInputElement).value).toBe("3");
  expect(box.getAttribute("min")).toBe("1");
  expect(box.getAttribute("max")).toBe("25");
  const button = screen.getByRole("button", { name: "Start a dispatch" });
  fireEvent.change(box, { target: { value: "26" } });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(box, { target: { value: "0" } });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(box, { target: { value: "25" } });
  expect((button as HTMLButtonElement).disabled).toBe(false);
});

it("starts a dispatch with the number of claim agents from the box", async () => {
  const fetchMock = mockApi(routes({ "POST /api/dispatches": run() }));
  render(<Queue />);
  fireEvent.change(await screen.findByLabelText("Claim agents at once"), {
    target: { value: "7" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Start a dispatch" }));
  confirmDialog("Start the dispatch");
  await vi.waitFor(() => {
    expect(calls(fetchMock)).toContain("POST /api/dispatches");
  });
  const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
  expect(post?.[1]?.body).toBe(JSON.stringify({ concurrency: 7 }));
});

it("starts with 3 when the box is left alone", async () => {
  const fetchMock = mockApi(routes({ "POST /api/dispatches": run() }));
  render(<Queue />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Start a dispatch" }),
  );
  confirmDialog("Start the dispatch");
  await vi.waitFor(() => {
    const post = fetchMock.mock.calls.find(
      ([, init]) => init?.method === "POST",
    );
    expect(post?.[1]?.body).toBe(JSON.stringify({ concurrency: 3 }));
  });
});

it("cannot start a dispatch while one is running", async () => {
  mockApi(routes({ "GET /api/dispatches/latest": run() }));
  render(<Queue />);
  const button = await screen.findByRole("button", {
    name: "Start a dispatch",
  });
  expect((button as HTMLButtonElement).disabled).toBe(true);
});

it("shows the refusal when another run is going", async () => {
  mockApi(
    routes({
      "POST /api/dispatches": new Failure(
        409,
        "A dispatch or evaluation is already running.",
      ),
    }),
  );
  render(<Queue />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Start a dispatch" }),
  );
  confirmDialog("Start the dispatch");
  expect((await screen.findByRole("alert")).textContent).toBe(
    "A dispatch or evaluation is already running.",
  );
});

it("says when the agent's service isn't running", async () => {
  mockApi(
    routes({
      "POST /api/dispatches": new Failure(503, "The agent isn't running."),
    }),
  );
  render(<Queue />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Start a dispatch" }),
  );
  confirmDialog("Start the dispatch");
  expect((await screen.findByRole("alert")).textContent).toBe(
    "The agent isn't running.",
  );
});

it("shows the dispatch's claims with their state, start, time and cost", async () => {
  mockApi(routes({ "GET /api/dispatches/latest": finishedRun }));
  render(<Queue />);
  const panel = await screen.findByRole("region", { name: "Current run" });
  expect(within(panel).getByText("Finished")).toBeDefined();
  expect(within(panel).getByText("$0.2468")).toBeDefined();
  expect(within(panel).getByText("1 min 23 s")).toBeDefined();
  expect(within(panel).getByText("openai/gpt-6-luna")).toBeDefined();
  const rows = within(within(panel).getByRole("table")).getAllByRole("row");
  expect(rows).toHaveLength(4);
  const first = within(rows[1]);
  expect(first.getByRole("link", { name: "CLM-1001" })).toBeDefined();
  expect(first.getByText("Decided")).toBeDefined();
  expect(first.getByText("Approved")).toBeDefined();
  expect(first.getByText("12.5 s")).toBeDefined();
  expect(first.getByText("$0.0123")).toBeDefined();
  expect(within(rows[2]).getByText("Waiting for sign-off")).toBeDefined();
});

it("shows the summary built from the records when the dispatch finishes", async () => {
  mockApi(routes({ "GET /api/dispatches/latest": finishedRun }));
  render(<Queue />);
  const panel = await screen.findByRole("region", { name: "Current run" });
  expect(
    within(panel).getByText("2 approved, 0 declined and 0 referred."),
  ).toBeDefined();
  expect(
    within(panel).getByText(/is waiting for a sign-off on \$1,250\.00/),
  ).toBeDefined();
  expect(within(panel).getByText(/is left for a handler\./)).toBeDefined();
  expect(within(panel).getByText(/is still in progress\./)).toBeDefined();
});

it("shows a running dispatch without its summary, and a stopped one as stopped", async () => {
  mockApi(routes({ "GET /api/dispatches/latest": run() }));
  const { unmount } = render(<Queue />);
  expect(await screen.findByText("Running")).toBeDefined();
  expect(screen.getByText("The dispatcher is choosing claims.")).toBeDefined();
  expect(screen.queryByRole("heading", { name: "Summary" })).toBeNull();
  unmount();
  mockApi(
    routes({
      "GET /api/dispatches/latest": run({
        status: "stopped",
        claims: [runClaim({ status: "in_progress", decision: null })],
      }),
    }),
  );
  render(<Queue />);
  expect(await screen.findByText("Stopped")).toBeDefined();
});

it("says when a finished dispatch had nothing to work", async () => {
  mockApi(
    routes({ "GET /api/dispatches/latest": run({ status: "finished" }) }),
  );
  render(<Queue />);
  expect(
    await screen.findByText("The dispatch had no claims to work."),
  ).toBeDefined();
});

it("shows no run panel before the first dispatch", async () => {
  mockApi(routes());
  render(<Queue />);
  await screen.findByRole("table");
  expect(screen.queryByRole("region", { name: "Current run" })).toBeNull();
});

it("refreshes while a dispatch is running and shows its claims filling in", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const first = run();
  const later = run({ claims: [runClaim({ status: "in_progress" })] });
  mockApi(
    routes({ "GET /api/dispatches/latest": new Replies([first, later]) }),
  );
  render(<Queue />);
  const panel = await screen.findByRole("region", { name: "Current run" });
  expect(within(panel).queryByRole("link", { name: "CLM-1001" })).toBeNull();
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 2));
  expect(within(panel).getByRole("link", { name: "CLM-1001" })).toBeDefined();
});

it("refreshes while a claim is in progress", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const fetchMock = mockApi(
    routes({ "GET /api/claims": [summary({ status: "in_progress" })] }),
  );
  render(<Queue />);
  await screen.findByRole("table");
  const before = fetchMock.mock.calls.length;
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 2));
  expect(fetchMock.mock.calls.length).toBeGreaterThan(before);
});

it("does not refresh when nothing is running", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const fetchMock = mockApi(
    routes({ "GET /api/dispatches/latest": finishedRun }),
  );
  render(<Queue />);
  await screen.findAllByRole("table");
  const before = fetchMock.mock.calls.length;
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 2));
  expect(fetchMock.mock.calls).toHaveLength(before);
});

it("shows an error when the queue cannot load", async () => {
  mockApi(
    routes({ "GET /api/claims": new Failure(500, "The backend is down.") }),
  );
  render(<Queue />);
  expect((await screen.findByRole("alert")).textContent).toContain(
    "The backend is down.",
  );
});

it("opens a confirmation before a dispatch and calls nothing on Cancel", async () => {
  const fetchMock = mockApi(routes({ "POST /api/dispatches": run() }));
  render(<Queue />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Start a dispatch" }),
  );
  expect(calls(fetchMock)).not.toContain("POST /api/dispatches");
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }),
  );
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(calls(fetchMock)).not.toContain("POST /api/dispatches");
});

it("shows the shared page header and right-aligns the claimed amounts", async () => {
  mockApi(routes());
  const { container } = render(<Queue />);
  expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe(
    "Claims queue",
  );
  expect(container.querySelector(".page-header .purpose")).not.toBeNull();
  const header = [...container.querySelectorAll("th")].findIndex(
    (th) => th.textContent === "Claimed",
  );
  const cell = container.querySelector("tbody tr")?.children[header];
  expect(cell?.className).toContain("number");
});
