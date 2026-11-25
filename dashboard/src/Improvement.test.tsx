import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Improvement } from "./Improvement";
import { renderSignedIn } from "./signedIn";
import { calls, marcus, mockApi, proposal, RECIPES, round } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const waiting = round({
  status: "waiting",
  stage: "change_waiting",
  pending: proposal(),
  proposals: [proposal()],
});

it("shows where the round has got to and the change waiting for a handler", async () => {
  mockApi({ "GET /api/rounds": [waiting], "GET /api/recipes": RECIPES });
  renderSignedIn(<Improvement />);
  const stages = await screen.findByRole("list", {
    name: "Where the round has got to",
  });
  expect(
    within(stages).getByText("Change waiting").getAttribute("aria-current"),
  ).toBe("step");
  const card = screen.getByRole("region", {
    name: "Proposed change waiting for a handler",
  });
  expect(within(card).getByText("Be brief.")).toBeTruthy();
  expect(within(card).getByText(/low to medium/)).toBeTruthy();
});

it("counts the evaluations in the stage", async () => {
  mockApi({
    "GET /api/rounds": [round({ stage: "evaluating", progress: 1 })],
    "GET /api/recipes": RECIPES,
  });
  renderSignedIn(<Improvement />);
  expect(await screen.findByText("Evaluations (2 of 3)")).toBeTruthy();
});

it("rejects with a reason, after a confirmation", async () => {
  const fetchMock = mockApi({
    "GET /api/rounds": [waiting],
    "GET /api/recipes": RECIPES,
    "POST /api/proposals/1/reject": waiting,
  });
  renderSignedIn(<Improvement />);
  fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
  const dialog = screen.getByRole("dialog");
  fireEvent.change(within(dialog).getByRole("textbox"), {
    target: { value: "Too short for a denial." },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "Reject" }));
  await waitFor(() => {
    expect(calls(fetchMock)).toContain("POST /api/proposals/1/reject");
  });
  const body = fetchMock.mock.calls.find(([url]) =>
    url.endsWith("/reject"),
  )?.[1]?.body;
  expect(body).toBe(JSON.stringify({ reason: "Too short for a denial." }));
});

it("starts a round only after a confirmation", async () => {
  const fetchMock = mockApi({
    "GET /api/rounds": [],
    "GET /api/recipes": RECIPES,
    "POST /api/rounds": round(),
  });
  renderSignedIn(<Improvement />);
  fireEvent.click(await screen.findByRole("button", { name: "Start a round" }));
  expect(calls(fetchMock)).not.toContain("POST /api/rounds");
  fireEvent.click(screen.getByRole("button", { name: "Start the round" }));
  await waitFor(() => {
    expect(calls(fetchMock)).toContain("POST /api/rounds");
  });
});

it("says what a handler who can't approve payouts can't do", async () => {
  mockApi({ "GET /api/rounds": [waiting], "GET /api/recipes": RECIPES });
  renderSignedIn(<Improvement />, marcus);
  expect(
    await screen.findAllByText(/Only a handler who can sign off payouts/),
  ).not.toHaveLength(0);
  expect(screen.getByRole("button", { name: "Stop the round" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
});

it("charts the held-out scores of the versions tried and lists the rounds", async () => {
  const kept = round({
    status: "finished",
    stage: "finished",
    version_tried: 2,
    reason: "Add a rule.",
    verdict: "kept",
    why: "accuracy improved",
    before: {
      decision_accuracy: 0.8,
      accuracy: 0.7,
      grade: 1.5,
      cost_per_claim: 0.01,
      seconds_per_claim: 10,
      runs: 2,
    },
    after: {
      decision_accuracy: 0.9,
      accuracy: 0.85,
      grade: 1.9,
      cost_per_claim: 0.01,
      seconds_per_claim: 10,
      runs: 2,
    },
  });
  mockApi({ "GET /api/rounds": [kept], "GET /api/recipes": RECIPES });
  renderSignedIn(<Improvement />);
  expect(
    await screen.findByRole("img", { name: /Held-out accuracy/ }),
  ).toBeTruthy();
  expect(screen.getByText("Kept")).toBeTruthy();
  expect(screen.getByText("85%")).toBeTruthy();
});

it("says what to do when there are no rounds", async () => {
  mockApi({ "GET /api/rounds": [], "GET /api/recipes": RECIPES });
  renderSignedIn(<Improvement />);
  expect(await screen.findByText("No rounds yet")).toBeTruthy();
});
