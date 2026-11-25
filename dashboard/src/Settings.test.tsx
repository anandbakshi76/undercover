import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { THEME_KEY } from "./hooks";
import { Settings } from "./Settings";
import { renderSignedIn } from "./signedIn";
import {
  calls,
  Failure,
  marcus,
  MODELS,
  mockApi,
  Replies,
  setting,
  settingChange,
  STARTING_SETTINGS,
} from "./testing";

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function routes(extra: Record<string, unknown> = {}) {
  return {
    "GET /api/settings": STARTING_SETTINGS,
    "GET /api/models": MODELS,
    "GET /api/settings/changes": [],
    ...extra,
  };
}

it("shows every setting with its unit and says none has been changed", async () => {
  mockApi(routes());
  renderSignedIn(<Settings />);
  expect(
    await screen.findByRole("heading", { name: "Settings" }),
  ).toBeDefined();
  expect(
    screen.getByLabelText("Limit", {
      selector: "#approval_limit-value",
    }),
  ).toHaveProperty("value", "1000");
  expect(screen.getByLabelText("Accuracy floor")).toHaveProperty(
    "value",
    "0.6",
  );
  expect(screen.getByLabelText("Grade floor")).toHaveProperty("value", "1");
  expect(screen.getByLabelText("Claim agents")).toHaveProperty("value", "25");
  expect(screen.getAllByText("Not changed since the start.")).toHaveLength(6);
  expect(
    screen.getAllByText("a fraction of the old value").length,
  ).toBeGreaterThan(0);
  expect(
    screen.getByText("No setting has been changed.", { exact: false }),
  ).toBeDefined();
});

it("lets a senior handler change a setting only after a confirmation", async () => {
  const fetchMock = mockApi(
    routes({
      "GET /api/settings": new Replies([
        STARTING_SETTINGS,
        STARTING_SETTINGS.map((item) =>
          item.key === "round_limit"
            ? setting("round_limit", 0.75, {
                changed_by: "Dana Whitfield",
                changed_at: "2026-11-25T11:00:00+00:00",
              })
            : item,
        ),
      ]),
      "GET /api/settings/changes": new Replies([[], [settingChange()]]),
      "PUT /api/settings/round_limit": setting("round_limit", 0.75),
    }),
  );
  renderSignedIn(<Settings />);
  const limit = await screen.findByLabelText("Limit", {
    selector: "#round_limit-value",
  });
  const save = screen.getByRole("button", {
    name: "Save spending limit for each round",
  });
  expect(save as HTMLButtonElement).toHaveProperty("disabled", true);
  fireEvent.change(limit, { target: { value: "0.75" } });
  fireEvent.click(save);
  const dialog = await screen.findByRole("dialog", {
    name: "Change spending limit for each round?",
  });
  expect(within(dialog).getByText("$0.50")).toBeDefined();
  expect(within(dialog).getByText("$0.75")).toBeDefined();
  expect(calls(fetchMock)).not.toContain("PUT /api/settings/round_limit");
  fireEvent.click(within(dialog).getByRole("button", { name: "Change it" }));
  await vi.waitFor(() => {
    expect(calls(fetchMock)).toContain("PUT /api/settings/round_limit");
  });
  const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(put?.[1]?.body).toBe(JSON.stringify({ value: 0.75 }));
  expect(
    await screen.findByText("Last changed by Dana Whitfield", { exact: false }),
  ).toBeDefined();
  expect(screen.queryByRole("dialog")).toBeNull();
  const changes = screen.getByRole("region", {
    name: "Changes to the settings",
  });
  expect(within(changes).getByText("Dana Whitfield")).toBeDefined();
});

it("sends a nested setting whole", async () => {
  const fetchMock = mockApi(
    routes({ "PUT /api/settings/objectives": setting("objectives", {}) }),
  );
  renderSignedIn(<Settings />);
  fireEvent.change(await screen.findByLabelText("Accuracy floor"), {
    target: { value: "0.7" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Save business objectives" }),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Change it" }));
  await vi.waitFor(() => {
    expect(calls(fetchMock)).toContain("PUT /api/settings/objectives");
  });
  const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(put?.[1]?.body).toBe(
    JSON.stringify({ value: { accuracy: 0.7, grade: 1 } }),
  );
});

it("changes nothing when the confirmation is cancelled", async () => {
  const fetchMock = mockApi(routes());
  renderSignedIn(<Settings />);
  fireEvent.change(await screen.findByLabelText("Claim agents"), {
    target: { value: "10" },
  });
  fireEvent.click(
    screen.getByRole("button", {
      name: "Save claim agents at once in the improver's evaluations",
    }),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(calls(fetchMock).some((call) => call.startsWith("PUT"))).toBe(false);
});

it("shows the backend's message when a change is refused", async () => {
  mockApi(
    routes({
      "PUT /api/settings/round_limit": new Failure(
        422,
        "round_limit: Input should be greater than 0",
      ),
    }),
  );
  renderSignedIn(<Settings />);
  fireEvent.change(
    await screen.findByLabelText("Limit", { selector: "#round_limit-value" }),
    { target: { value: "0" } },
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Save spending limit for each round" }),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Change it" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "round_limit: Input should be greater than 0",
  );
});

it("tells another handler why they can't change anything, and shows the values", async () => {
  mockApi(routes());
  renderSignedIn(<Settings />, marcus);
  expect(
    await screen.findByText(
      "Marcus Oyelaran can't change the settings. Only a handler who can sign off payouts over the limit may.",
    ),
  ).toBeDefined();
  expect(screen.getByLabelText("Accuracy floor")).toHaveProperty(
    "disabled",
    true,
  );
  expect(screen.queryByRole("button", { name: /^Save / })).toBeNull();
  expect(screen.getByLabelText("Accuracy floor")).toHaveProperty(
    "value",
    "0.6",
  );
});

it("lists the models allowed with their prices, set in a file", async () => {
  mockApi(routes());
  renderSignedIn(<Settings />);
  const models = await screen.findByRole("region", { name: "Models allowed" });
  expect(within(models).getByText("openai/gpt-6-luna")).toBeDefined();
  expect(within(models).getByText("none, low, medium")).toBeDefined();
  expect(within(models).getByText("$0.10")).toBeDefined();
  expect(within(models).getByText("$0.50")).toBeDefined();
  expect(within(models).getByText("data/models.json")).toBeDefined();
});

it("sets the appearance and the browser remembers it", async () => {
  mockApi(routes());
  renderSignedIn(<Settings />);
  const dark = await screen.findByRole("radio", { name: "Dark" });
  const system = screen.getByRole("radio", {
    name: "The same as the computer",
  });
  expect(system).toHaveProperty("checked", true);
  await act(async () => {
    fireEvent.click(dark);
    await Promise.resolve();
  });
  expect(localStorage.getItem(THEME_KEY)).toBe("dark");
  expect(dark).toHaveProperty("checked", true);
  await act(async () => {
    fireEvent.click(screen.getByRole("radio", { name: "Light" }));
    await Promise.resolve();
  });
  await act(async () => {
    fireEvent.click(system);
    await Promise.resolve();
  });
  expect(localStorage.getItem(THEME_KEY)).toBeNull();
});

it("lists the changes with who made them and a nested value in words", async () => {
  mockApi(
    routes({
      "GET /api/settings/changes": [
        settingChange(),
        settingChange({
          key: "objectives",
          old: { accuracy: 0.6, grade: 1 },
          new: { accuracy: 0.7, grade: 1 },
          changed_at: "2026-11-25T10:00:00+00:00",
        }),
      ],
    }),
  );
  renderSignedIn(<Settings />);
  const changes = await screen.findByRole("region", {
    name: "Changes to the settings",
  });
  expect(within(changes).getByText("accuracy 0.7, grade 1")).toBeDefined();
  expect(within(changes).getByText("0.75")).toBeDefined();
  expect(within(changes).getAllByText("Dana Whitfield")).toHaveLength(2);
});

it("shows an error that says what to do when the settings can't load", async () => {
  mockApi(
    routes({ "GET /api/settings": new Failure(500, "The backend is down.") }),
  );
  renderSignedIn(<Settings />);
  expect((await screen.findByRole("alert")).textContent).toContain(
    "The backend is down.",
  );
  expect(
    screen.getByText(
      "Check that the backend is running, then reload the page.",
    ),
  ).toBeDefined();
});

it("shows nothing for a visitor who is not signed in", () => {
  mockApi(routes());
  const { container } = render(<Settings />);
  expect(container.textContent).toBe("");
});
