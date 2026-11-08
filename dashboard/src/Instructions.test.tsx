import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Instructions } from "./Instructions";
import { Failure, mockApi } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows both sets of instructions read only", async () => {
  mockApi({
    "GET /api/instructions": {
      queue: "Plan the run as a to-do list.",
      claim: "Decide one claim.",
    },
  });
  render(<Instructions />);
  const queue = await screen.findByRole("region", {
    name: "Running the queue",
  });
  expect(
    within(queue).getByText("Plan the run as a to-do list."),
  ).toBeDefined();
  const claim = screen.getByRole("region", { name: "Working a claim" });
  expect(within(claim).getByText("Decide one claim.")).toBeDefined();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
});

it("shows an error when the instructions cannot load", async () => {
  mockApi({ "GET /api/instructions": new Failure(500, "Not available.") });
  render(<Instructions />);
  expect((await screen.findByRole("alert")).textContent).toBe("Not available.");
});
