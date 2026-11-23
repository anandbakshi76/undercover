import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { POLL_MS } from "./hooks";
import { Queue } from "./Queue";
import { Failure, mockApi, summary } from "./testing";

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

function count(label: string): string | null {
  const counts = screen.getByRole("list", { name: "Claims by state" });
  const item = within(counts).getByText(label).closest("li");
  return item?.querySelector(".count-number")?.textContent ?? null;
}

it("lists the claims with their states and amounts", async () => {
  mockApi({ "GET /api/claims": claims });
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
  mockApi({ "GET /api/claims": claims });
  render(<Queue />);
  await screen.findByRole("table");
  expect(count("Waiting")).toBe("1");
  expect(count("In progress")).toBe("0");
  expect(count("Waiting for sign-off")).toBe("1");
  expect(count("Decided")).toBe("1");
  expect(count("Needs a handler")).toBe("1");
});

it("refreshes while a claim is in progress", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const fetchMock = mockApi({
    "GET /api/claims": [summary({ status: "in_progress" })],
  });
  render(<Queue />);
  await screen.findByRole("table");
  const before = fetchMock.mock.calls.length;
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 2));
  expect(fetchMock.mock.calls.length).toBeGreaterThan(before);
});

it("does not refresh when no claim is in progress", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const fetchMock = mockApi({ "GET /api/claims": claims });
  render(<Queue />);
  await screen.findByRole("table");
  const before = fetchMock.mock.calls.length;
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 2));
  expect(fetchMock.mock.calls).toHaveLength(before);
});

it("shows an error when the queue cannot load", async () => {
  mockApi({ "GET /api/claims": new Failure(500, "The backend is down.") });
  render(<Queue />);
  expect((await screen.findByRole("alert")).textContent).toBe(
    "The backend is down.",
  );
});
