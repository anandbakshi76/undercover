import {
  act,
  cleanup,
  fireEvent,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Decision } from "./api";
import { ClaimPage } from "./ClaimPage";
import { renderSignedIn } from "./signedIn";
import { dateTime } from "./format";
import { POLL_MS } from "./hooks";
import {
  confirmDialog,
  calls,
  claim,
  Failure,
  handlers,
  marcus,
  mockApi,
  policy,
  Replies,
  step,
} from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const approval: Decision = {
  decision: "approve",
  payout: 1250,
  reason: "The suitcase was lost by the airline. The items are covered.",
  policy_section: "Section 5, Baggage and Personal Effects",
  decided_at: "2026-10-28T10:00:00+00:00",
};

function routes(overrides: Record<string, unknown> = {}) {
  return {
    "GET /api/claims/CLM-1001": claim(),
    "GET /api/claims/CLM-1001/steps": [],
    "GET /api/policies/POL-2001": policy,
    "GET /api/handlers": handlers,
    ...overrides,
  };
}

it("shows a waiting claim in full and runs the agent", async () => {
  const fetchMock = mockApi(
    routes({ "POST /api/claims/CLM-1001/run": { status: "in_progress" } }),
  );
  renderSignedIn(<ClaimPage claimId="CLM-1001" />);
  expect(
    await screen.findByText("My flight home from Rome was canceled."),
  ).toBeDefined();
  expect(screen.getByText("Airport hotel, one night")).toBeDefined();
  expect(screen.getByText("Hotel Aurelia. Total paid: $245.00.")).toBeDefined();
  expect(screen.getByText("UnderCover Premium")).toBeDefined();
  expect(screen.getByText("Electronics Plus")).toBeDefined();
  expect(screen.getByText("No decision yet.")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Run agent" }));
  await vi.waitFor(() => {
    expect(calls(fetchMock)).toContain("POST /api/claims/CLM-1001/run");
  });
});

it("shows the agent's steps as they arrive", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  mockApi(
    routes({
      "GET /api/claims/CLM-1001": claim({ status: "in_progress" }),
      "GET /api/claims/CLM-1001/steps": new Replies([
        [step(1)],
        [step(1), step(2, { kind: "tool_result", text: "get_claim: {}" })],
      ]),
    }),
  );
  renderSignedIn(<ClaimPage claimId="CLM-1001" />);
  const list = await screen.findByRole("list", { name: "Agent steps" });
  expect(within(list).getAllByRole("listitem")).toHaveLength(1);
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS));
  const items = within(list).getAllByRole("listitem");
  expect(items).toHaveLength(2);
  expect(items[1]?.textContent).toBe("Resultget_claim: {}");
  expect(screen.queryByRole("button", { name: "Run agent" })).toBeNull();
});

it("shows a decided claim with its decision", async () => {
  mockApi(
    routes({
      "GET /api/claims/CLM-1001": claim({
        status: "decided",
        decision: {
          ...approval,
          decision: "decline",
          payout: 0,
          reason: "Lost through being left unattended.",
        },
      }),
      "GET /api/claims/CLM-1001/steps": [
        step(1, { kind: "reply", text: "Declined." }),
      ],
    }),
  );
  renderSignedIn(<ClaimPage claimId="CLM-1001" />);
  expect(await screen.findByText("Declined")).toBeDefined();
  expect(screen.getByText("$0.00")).toBeDefined();
  expect(screen.getByText("Lost through being left unattended.")).toBeDefined();
  expect(
    screen.getByText("Based on: Section 5, Baggage and Personal Effects"),
  ).toBeDefined();
  expect(screen.queryByRole("button", { name: "Run agent" })).toBeNull();
  expect(screen.queryByRole("combobox")).toBeNull();
});

const SIGNED_OFF = {
  handler_id: "HND-01",
  signed_off_at: "2026-10-28T10:05:00+00:00",
};
const PAID = {
  amount: 1250,
  paid_at: "2026-10-28T10:06:00+00:00",
  signed_off_by: "HND-01",
  signed_off_at: "2026-10-28T10:05:00+00:00",
};

it("signs off a payout over the limit and follows it until it is paid", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const fetchMock = mockApi(
    routes({
      "GET /api/claims/CLM-1001": new Replies([
        claim({ status: "awaiting_sign_off", decision: approval }),
        claim({
          status: "in_progress",
          decision: approval,
          sign_off: SIGNED_OFF,
        }),
        claim({
          status: "decided",
          decision: approval,
          sign_off: SIGNED_OFF,
          payment: PAID,
        }),
      ]),
      "POST /api/claims/CLM-1001/sign-off": claim(),
    }),
  );
  renderSignedIn(<ClaimPage claimId="CLM-1001" />);
  expect(
    await screen.findByText("The payment waits for a handler's sign-off."),
  ).toBeDefined();
  expect(
    screen.getByText("Dana Whitfield", { selector: "strong" }),
  ).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Sign off" }));
  confirmDialog("Sign off");
  expect(
    await screen.findByText(
      `Payment pending, signed off by Dana Whitfield at ${dateTime(SIGNED_OFF.signed_off_at)}`,
    ),
  ).toBeDefined();
  expect(screen.getByText("In progress")).toBeDefined();
  expect(screen.queryByRole("button", { name: "Sign off" })).toBeNull();
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS));
  expect(
    await screen.findByText(
      `on ${dateTime(PAID.paid_at)}, signed off by Dana Whitfield at ${dateTime(PAID.signed_off_at)}`,
      { exact: false },
    ),
  ).toBeDefined();
  expect(
    screen.getByText("$1,250.00", { selector: ".payment .mono" }),
  ).toBeDefined();
  expect(calls(fetchMock)).toContain("POST /api/claims/CLM-1001/sign-off");
});

it("shows the backend's message when sign-off fails", async () => {
  mockApi(
    routes({
      "GET /api/claims/CLM-1001": claim({
        status: "awaiting_sign_off",
        decision: approval,
      }),
      "POST /api/claims/CLM-1001/sign-off": new Failure(
        409,
        "Claim CLM-1001 is not waiting for sign-off.",
      ),
    }),
  );
  renderSignedIn(<ClaimPage claimId="CLM-1001" />);
  fireEvent.click(await screen.findByRole("button", { name: "Sign off" }));
  confirmDialog("Sign off");
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Claim CLM-1001 is not waiting for sign-off.",
  );
});

it("shows an approval that has not been paid", async () => {
  mockApi(
    routes({
      "GET /api/claims/CLM-1001": claim({
        status: "decided",
        decision: { ...approval, payout: 420 },
      }),
    }),
  );
  renderSignedIn(<ClaimPage claimId="CLM-1001" />);
  expect(await screen.findByText("Payment pending")).toBeDefined();
});

it("shows an error when the claim cannot load", async () => {
  mockApi(
    routes({
      "GET /api/claims/CLM-1001": new Failure(
        404,
        "There is no claim CLM-1001.",
      ),
    }),
  );
  renderSignedIn(<ClaimPage claimId="CLM-1001" />);
  expect((await screen.findByRole("alert")).textContent).toContain(
    "There is no claim CLM-1001.",
  );
});

it("keeps refreshing while the claim is in progress and stops once it is paid", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const small = { ...approval, payout: 420 };
  mockApi(
    routes({
      "GET /api/claims/CLM-1001/steps": [step(1)],
      "GET /api/claims/CLM-1001": new Replies([
        claim({ status: "in_progress", decision: small }),
        claim({
          status: "decided",
          decision: small,
          payment: {
            ...PAID,
            amount: 420,
            signed_off_by: null,
            signed_off_at: null,
          },
        }),
      ]),
    }),
  );
  renderSignedIn(<ClaimPage claimId="CLM-1001" />);
  expect(await screen.findByText("Working...")).toBeDefined();
  expect(screen.getByText("Payment pending")).toBeDefined();
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS));
  expect(screen.queryByText("Working...")).toBeNull();
  expect(
    screen.getByText("$420.00", { selector: ".payment .mono" }),
  ).toBeDefined();
  expect(screen.queryByText("signed off", { exact: false })).toBeNull();
});

it("shows a Run agent error, such as the agent not running", async () => {
  mockApi(
    routes({
      "POST /api/claims/CLM-1001/run": new Failure(
        503,
        "The agent isn't running.",
      ),
    }),
  );
  renderSignedIn(<ClaimPage claimId="CLM-1001" />);
  fireEvent.click(await screen.findByRole("button", { name: "Run agent" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "The agent isn't running.",
  );
});

it("lists the sections the agent read, once each and in order", async () => {
  mockApi(
    routes({
      "GET /api/claims/CLM-1001/steps": [
        step(1, { kind: "section", text: "Policy wording 3. Trip Delay" }),
        step(2, {
          kind: "section",
          text: "Claims manual 10. Meals without receipts",
        }),
        step(3, { kind: "section", text: "Policy wording 3. Trip Delay" }),
        step(4, { kind: "reply", text: "Approved." }),
      ],
    }),
  );
  renderSignedIn(<ClaimPage claimId="CLM-1001" />);
  const read = await screen.findByRole("list", { name: "Sections read" });
  expect(
    within(read)
      .getAllByRole("listitem")
      .map((item) => item.textContent),
  ).toEqual([
    "Policy wording 3. Trip Delay",
    "Claims manual 10. Meals without receipts",
  ]);
  const steps = screen.getByRole("list", { name: "Agent steps" });
  expect(within(steps).getAllByText("Read")).toHaveLength(3);
});

it("says when the agent has read nothing yet", async () => {
  mockApi(routes());
  renderSignedIn(<ClaimPage claimId="CLM-1001" />);
  expect(
    await screen.findByText("The agent has not read any sections yet."),
  ).toBeDefined();
});

it("shows a claim left for a handler, with no Run agent button", async () => {
  mockApi(
    routes({
      "GET /api/claims/CLM-1001": claim({
        status: "needs_handler",
        attempts: 2,
      }),
    }),
  );
  renderSignedIn(<ClaimPage claimId="CLM-1001" />);
  expect(
    await screen.findByText(
      "The agent could not decide this claim after two attempts. It is left for a handler.",
    ),
  ).toBeDefined();
  expect(screen.getAllByText("Needs a handler").length).toBeGreaterThan(0);
  expect(screen.queryByRole("button", { name: "Run agent" })).toBeNull();
});

it("shows an evaluation copy with a note, a way back, and no sign-off form or Run agent button", async () => {
  mockApi(
    routes({
      "GET /api/claims/CLM-1001": claim({
        claim_id: "CLM-1001",
        status: "awaiting_sign_off",
        decision: approval,
        evaluation_id: "EVAL-0001",
        source_id: "CLM-1001",
      }),
    }),
  );
  renderSignedIn(<ClaimPage claimId="CLM-1001" />);
  expect(await screen.findByText(/worked for an evaluation/)).toBeDefined();
  expect(
    screen
      .getByRole("link", { name: "Back to the evaluation" })
      .getAttribute("href"),
  ).toBe("#/evaluations/EVAL-0001");
  expect(screen.queryByRole("button", { name: /Sign off/ })).toBeNull();
  expect(screen.queryByRole("button", { name: "Run agent" })).toBeNull();
});

it("shows no Run agent button on a waiting evaluation copy", async () => {
  mockApi(
    routes({
      "GET /api/claims/CLM-1001": claim({
        evaluation_id: "EVAL-0001",
        source_id: "CLM-1001",
      }),
    }),
  );
  renderSignedIn(<ClaimPage claimId="CLM-1001" />);
  await screen.findByText(/worked for an evaluation/);
  expect(screen.queryByRole("button", { name: "Run agent" })).toBeNull();
});

it("tells a handler who can't sign off why, with no button", async () => {
  mockApi(
    routes({
      "GET /api/claims/CLM-1001": claim({
        status: "awaiting_sign_off",
        decision: approval,
      }),
    }),
  );
  renderSignedIn(<ClaimPage claimId="CLM-1001" />, marcus);
  expect(
    await screen.findByText(
      "Marcus Oyelaran can't sign off this payout. Only a handler who can sign off payouts over the limit may.",
    ),
  ).toBeDefined();
  expect(screen.queryByRole("button", { name: "Sign off" })).toBeNull();
});
