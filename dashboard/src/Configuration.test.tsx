import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Configuration } from "./Configuration";
import {
  configDiff,
  currentConfiguration,
  Failure,
  mockApi,
  version,
} from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const second = version({
  version: 2,
  is_current: false,
  model: "google/gemini-3.5-flash-lite",
  reasoning_effort: "minimal",
  reason: "Add a rule about unattended bags.",
  proposed_by: "The improver",
  approved_by: "Dana Whitfield",
  scores: {
    evaluation: null,
    held_out: {
      decision_accuracy: 0.8,
      accuracy: 0.76,
      grade: 1.4,
      cost_per_claim: 0.0021,
      seconds_per_claim: 31.5,
      runs: 2,
    },
  },
});

const secondDiff = configDiff({
  version: 2,
  previous: 1,
  is_current: false,
  model: { before: "openai/gpt-6-luna", after: "google/gemini-3.5-flash-lite" },
  reasoning_effort: { before: "low", after: "minimal" },
  reason: "Add a rule about unattended bags.",
  proposed_by: "The improver",
  approved_by: "Dana Whitfield",
  instructions: [
    { kind: "same", text: "Decide the claim." },
    { kind: "removed", text: "Old rule." },
    { kind: "added", text: "Never approve an unattended bag." },
  ],
});

function routes(extra: Record<string, unknown> = {}) {
  return {
    "GET /api/configurations": [version(), second],
    "GET /api/configurations/current": currentConfiguration(),
    "GET /api/configurations/1/diff": configDiff(),
    "GET /api/configurations/2/diff": secondDiff,
    ...extra,
  };
}

it("shows the current version with its instructions, model and effort", async () => {
  mockApi(routes());
  render(<Configuration />);
  const current = await screen.findByRole("region", {
    name: "Current version",
  });
  expect(within(current).getByText("Current version: 1")).toBeDefined();
  expect(within(current).getByText("openai/gpt-6-luna")).toBeDefined();
  expect(within(current).getByText("low")).toBeDefined();
  expect(within(current).getByText(/Decide one claim\./)).toBeDefined();
  expect(within(current).getByText(/by UnderCover on/)).toBeDefined();
});

it("lists every version, newest first, with its held-out scores", async () => {
  mockApi(routes());
  render(<Configuration />);
  const table = await screen.findByRole("region", { name: "Versions" });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(2);
  expect(
    within(rows[0]).getByText("Add a rule about unattended bags."),
  ).toBeDefined();
  expect(within(rows[0]).getByText("76%")).toBeDefined();
  expect(within(rows[0]).getByText("1.40")).toBeDefined();
  expect(within(rows[0]).getByText("$0.0021")).toBeDefined();
  expect(within(rows[0]).getByText("31.5 s")).toBeDefined();
  expect(within(rows[0]).queryByText("Current")).toBeNull();
  expect(within(rows[1]).getByText("Current")).toBeDefined();
  expect(within(rows[1]).getByText("Not evaluated yet")).toBeDefined();
});

it("shows version 1 as the starting point with nothing to compare", async () => {
  mockApi(routes());
  render(<Configuration />);
  const changes = await screen.findByRole("region", {
    name: "Changes in version 1",
  });
  expect(within(changes).getByText("Version 1")).toBeDefined();
  expect(
    within(changes).getByText(
      "Version 1 is the claim agent as it was before the improver",
      {
        exact: false,
      },
    ),
  ).toBeDefined();
});

it("shows what a version changed from the one before as a diff", async () => {
  mockApi(routes());
  render(<Configuration />);
  await screen.findByRole("region", { name: "Versions" });
  fireEvent.click(screen.getByRole("button", { name: "Show changes in 2" }));
  const changes = await screen.findByRole("region", {
    name: "Changes in version 2",
  });
  expect(
    within(changes).getByText("Version 2 compared with version 1"),
  ).toBeDefined();
  expect(
    within(changes).getByText(
      "openai/gpt-6-luna to google/gemini-3.5-flash-lite",
    ),
  ).toBeDefined();
  expect(within(changes).getByText("low to minimal")).toBeDefined();
  const diff = within(changes).getByRole("group", {
    name: "Changes to the instructions",
  });
  expect(diff.querySelector(".diff-added")?.textContent).toContain(
    "Never approve an unattended bag.",
  );
  expect(diff.querySelector(".diff-removed")?.textContent).toContain(
    "Old rule.",
  );
  expect(
    within(changes).getByText(
      /Proposed by The improver, approved by Dana Whitfield on/,
    ),
  ).toBeDefined();
});

it("says when a version's model and effort did not change", async () => {
  mockApi(
    routes({
      "GET /api/configurations/2/diff": {
        ...secondDiff,
        model: { before: "openai/gpt-6-luna", after: "openai/gpt-6-luna" },
        reasoning_effort: { before: "low", after: "low" },
        instructions: [],
      },
    }),
  );
  render(<Configuration />);
  await screen.findByRole("region", { name: "Versions" });
  fireEvent.click(screen.getByRole("button", { name: "Show changes in 2" }));
  const changes = await screen.findByRole("region", {
    name: "Changes in version 2",
  });
  expect(within(changes).getByText("openai/gpt-6-luna")).toBeDefined();
  expect(within(changes).getByText("No changes to the text.")).toBeDefined();
});

it("shows an error that says what to do when the configuration can't load", async () => {
  mockApi(
    routes({
      "GET /api/configurations": new Failure(500, "The backend is down."),
    }),
  );
  render(<Configuration />);
  expect((await screen.findByRole("alert")).textContent).toContain(
    "The backend is down.",
  );
});

it("says when a version's changes can't load", async () => {
  mockApi(
    routes({
      "GET /api/configurations/1/diff": new Failure(
        404,
        "There is no version 1.",
      ),
    }),
  );
  render(<Configuration />);
  expect((await screen.findByRole("alert")).textContent).toContain(
    "There is no version 1.",
  );
});

it("marks a version without a grade as not graded", async () => {
  mockApi(
    routes({
      "GET /api/configurations": [
        version({
          scores: {
            evaluation: null,
            held_out: {
              decision_accuracy: 1,
              accuracy: 1,
              grade: null,
              cost_per_claim: 0,
              seconds_per_claim: 1,
              runs: 2,
            },
          },
        }),
      ],
    }),
  );
  render(<Configuration />);
  expect(await screen.findByText("Not graded")).toBeDefined();
});
