import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { parseRoute, THEME_KEY } from "./hooks";
import { renderSignedIn } from "./signedIn";
import { claim, evaluationDetail, mockApi, policy, summary } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

describe("parseRoute", () => {
  it("reads each page from the hash", () => {
    expect(parseRoute("")).toEqual({ page: "queue" });
    expect(parseRoute("#/")).toEqual({ page: "queue" });
    expect(parseRoute("#/instructions")).toEqual({ page: "instructions" });
    expect(parseRoute("#/claims/CLM-1001")).toEqual({
      page: "claim",
      claimId: "CLM-1001",
    });
    expect(parseRoute("#/scoreboard")).toEqual({ page: "scoreboard" });
    expect(parseRoute("#/evaluations/EVAL-0001")).toEqual({
      page: "evaluation",
      runId: "EVAL-0001",
    });
    expect(parseRoute("#/elsewhere")).toEqual({ page: "queue" });
  });
});

describe("App", () => {
  it("shows the queue by default", async () => {
    mockApi({
      "GET /api/claims": [summary()],
      "GET /api/dispatches/latest": null,
    });
    renderSignedIn(<App />);
    expect(
      await screen.findByRole("heading", { name: "Claims queue" }),
    ).toBeDefined();
  });

  it("shows the scoreboard and an evaluation from the hash", async () => {
    mockApi({
      "GET /api/evaluations": [],
      "GET /api/evaluations/EVAL-0001": evaluationDetail(),
    });
    window.location.hash = "#/scoreboard";
    renderSignedIn(<App />);
    expect(
      await screen.findByRole("heading", { name: "Scoreboard" }),
    ).toBeDefined();
    expect(
      screen
        .getByRole("link", { name: "Scoreboard" })
        .getAttribute("aria-current"),
    ).toBe("page");
    act(() => {
      window.location.hash = "#/evaluations/EVAL-0001";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(await screen.findByLabelText("Measures")).toBeDefined();
  });

  it("shows the instructions page", async () => {
    mockApi({
      "GET /api/instructions": {
        claim: "Decide claims.",
      },
    });
    window.location.hash = "#/instructions";
    renderSignedIn(<App />);
    expect(await screen.findByText("Decide claims.")).toBeDefined();
  });

  it("shows a claim page and follows the hash", async () => {
    mockApi({
      "GET /api/claims": [summary()],
      "GET /api/dispatches/latest": null,
      "GET /api/claims/CLM-1001": claim(),
      "GET /api/claims/CLM-1001/steps": [],
      "GET /api/policies/POL-2001": policy,
      "GET /api/handlers": [],
    });
    renderSignedIn(<App />);
    await screen.findByRole("heading", { name: "Claims queue" });
    act(() => {
      window.location.hash = "#/claims/CLM-1001";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(
      await screen.findByRole("heading", { name: /CLM-1001/ }),
    ).toBeDefined();
  });
});

describe("dark mode", () => {
  it("starts in the theme set on the page", async () => {
    mockApi({
      "GET /api/claims": [summary()],
      "GET /api/dispatches/latest": null,
    });
    document.documentElement.dataset.theme = "dark";
    renderSignedIn(<App />);
    const toggle = await screen.findByRole("button", { name: "Dark mode" });
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
  });

  it("switches between light and dark and remembers the choice", async () => {
    mockApi({
      "GET /api/claims": [summary()],
      "GET /api/dispatches/latest": null,
    });
    renderSignedIn(<App />);
    const toggle = await screen.findByRole("button", { name: "Dark mode" });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    fireEvent.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
  });
});
