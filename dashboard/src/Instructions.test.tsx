import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Instructions } from "./Instructions";
import { Failure, mockApi } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the claim agent's instructions read only", async () => {
  mockApi({ "GET /api/instructions": { claim: "Decide one claim." } });
  render(<Instructions />);
  const claim = await screen.findByRole("region", { name: "Working a claim" });
  expect(within(claim).getByText("Decide one claim.")).toBeDefined();
  expect(
    screen.queryByRole("region", { name: "Running the queue" }),
  ).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
});

it("shows an error when the instructions cannot load", async () => {
  mockApi({ "GET /api/instructions": new Failure(500, "Not available.") });
  render(<Instructions />);
  expect((await screen.findByRole("alert")).textContent).toBe("Not available.");
});
