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
import { calls, Failure, mockApi, Replies, run, summary } from "./testing";

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
  claim_limit: 3,
  claims_worked: ["CLM-1002", "CLM-1003", "CLM-1004"],
  todos: [
    { content: "Work CLM-1002", status: "done" },
    { content: "Work CLM-1003", status: "done" },
    { content: "Work CLM-1004", status: "left_for_handler" },
    { content: "Check for missed claims", status: "to_do" },
  ],
  summary: "Worked 3 claims. CLM-1004 is left for a handler.",
});

function routes(extra: Record<string, unknown> = {}) {
  return {
    "GET /api/claims": claims,
    "GET /api/queue/latest-run": null,
    ...extra,
  };
}

function count(label: string): string | null {
  const counts = screen.getByRole("list", { name: "Claims by state" });
  const item = within(counts).getByText(label).closest("li");
  return item?.querySelector(".count-number")?.textContent ?? null;
}

function todoStates(): string[] {
  const list = screen.getByRole("list", { name: "To-do list" });
  return within(list)
    .getAllByRole("listitem")
    .map((item) => item.textContent);
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

it("shows the latest run's to-do list and its summary", async () => {
  mockApi(routes({ "GET /api/queue/latest-run": finishedRun }));
  render(<Queue />);
  await screen.findByRole("region", { name: "Current run" });
  expect(screen.getByText("Finished")).toBeDefined();
  expect(todoStates()).toEqual([
    "DoneWork CLM-1002",
    "DoneWork CLM-1003",
    "Left for a handlerWork CLM-1004",
    "To doCheck for missed claims",
  ]);
  expect(
    screen.getByText("Worked 3 claims. CLM-1004 is left for a handler."),
  ).toBeDefined();
});

it("says when a running run has no to-do list yet", async () => {
  mockApi(routes({ "GET /api/queue/latest-run": run() }));
  render(<Queue />);
  expect(
    await screen.findByText("The agent is planning its work."),
  ).toBeDefined();
  expect(screen.getByText("Running")).toBeDefined();
});

it("shows no run panel before the first run", async () => {
  mockApi(routes());
  render(<Queue />);
  await screen.findByRole("table");
  expect(screen.queryByRole("region", { name: "Current run" })).toBeNull();
});

it("refreshes while a run is running and shows the to-do list moving on", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const first = run({ todos: [{ content: "Work CLM-1001", status: "to_do" }] });
  const later = run({
    todos: [{ content: "Work CLM-1001", status: "in_progress" }],
  });
  const fetchMock = mockApi(
    routes({ "GET /api/queue/latest-run": new Replies([first, later]) }),
  );
  render(<Queue />);
  await screen.findByRole("region", { name: "Current run" });
  expect(todoStates()).toEqual(["To doWork CLM-1001"]);
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 2));
  expect(todoStates()).toEqual(["In progressWork CLM-1001"]);
  expect(fetchMock.mock.calls.length).toBeGreaterThan(2);
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
    routes({ "GET /api/queue/latest-run": finishedRun }),
  );
  render(<Queue />);
  await screen.findByRole("table");
  const before = fetchMock.mock.calls.length;
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 2));
  expect(fetchMock.mock.calls).toHaveLength(before);
});

it("runs the queue on 5 claims unless told otherwise", async () => {
  const fetchMock = mockApi(routes({ "POST /api/queue/run": run() }));
  render(<Queue />);
  const box = await screen.findByLabelText("Claims to work");
  expect((box as HTMLInputElement).value).toBe("5");
  fireEvent.click(screen.getByRole("button", { name: "Run the queue" }));
  await vi.waitFor(() => {
    expect(calls(fetchMock)).toContain("POST /api/queue/run");
  });
  const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
  expect(post?.[1]?.body).toBe(JSON.stringify({ limit: 5 }));
});

it("sends the number of claims from the box", async () => {
  const fetchMock = mockApi(routes({ "POST /api/queue/run": run() }));
  render(<Queue />);
  fireEvent.change(await screen.findByLabelText("Claims to work"), {
    target: { value: "2" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Run the queue" }));
  await vi.waitFor(() => {
    const post = fetchMock.mock.calls.find(
      ([, init]) => init?.method === "POST",
    );
    expect(post?.[1]?.body).toBe(JSON.stringify({ limit: 2 }));
  });
});

it("cannot start a run while one is running", async () => {
  mockApi(routes({ "GET /api/queue/latest-run": run() }));
  render(<Queue />);
  const button = await screen.findByRole("button", { name: "Run the queue" });
  expect((button as HTMLButtonElement).disabled).toBe(true);
});

it("shows an error from running the queue", async () => {
  mockApi(
    routes({
      "POST /api/queue/run": new Failure(503, "The agent isn't running."),
    }),
  );
  render(<Queue />);
  fireEvent.click(await screen.findByRole("button", { name: "Run the queue" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "The agent isn't running.",
  );
});

it("shows an error when the queue cannot load", async () => {
  mockApi(
    routes({ "GET /api/claims": new Failure(500, "The backend is down.") }),
  );
  render(<Queue />);
  expect((await screen.findByRole("alert")).textContent).toBe(
    "The backend is down.",
  );
});
