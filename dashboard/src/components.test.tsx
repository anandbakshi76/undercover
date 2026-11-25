import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";
import { DiffView } from "./DiffView";
import { PageHeader } from "./PageHeader";
import { Empty, ErrorState, Loading } from "./States";

afterEach(cleanup);

it("says what a page is for in its header, with its actions", () => {
  render(
    <PageHeader title="Settings" purpose="The platform's settings.">
      <button type="button">Do it</button>
    </PageHeader>,
  );
  expect(screen.getByRole("heading", { name: "Settings" })).toBeDefined();
  expect(screen.getByText("The platform's settings.")).toBeDefined();
  expect(screen.getByRole("button", { name: "Do it" })).toBeDefined();
});

it("has no actions area when a page has no actions", () => {
  const { container } = render(<PageHeader title="A" purpose="B" />);
  expect(container.querySelector(".page-actions")).toBeNull();
});

it("shows additions and removals with a word as well as a colour", () => {
  render(
    <DiffView
      label="Changes"
      lines={[
        { kind: "gap", text: "..." },
        { kind: "same", text: "kept" },
        { kind: "removed", text: "old line" },
        { kind: "added", text: "new line" },
      ]}
    />,
  );
  const diff = screen.getByRole("group", { name: "Changes" });
  expect(diff.querySelector(".diff-added")?.textContent).toContain(
    "Added: new line",
  );
  expect(diff.querySelector(".diff-removed")?.textContent).toContain(
    "Removed: old line",
  );
  expect(diff.querySelector(".diff-same")?.textContent).toContain("kept");
  expect(diff.querySelector(".diff-gap")).not.toBeNull();
});

it("says so when a diff has no changes", () => {
  render(<DiffView label="Changes" lines={[]} />);
  expect(screen.getByText("No changes to the text.")).toBeDefined();
});

it("asks before it acts, and cancels with the button or Escape", () => {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <ConfirmDialog
      title="Change it?"
      confirmLabel="Change it"
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      <p>From 1 to 2.</p>
    </ConfirmDialog>,
  );
  expect(screen.getByRole("dialog", { name: "Change it?" })).toBeDefined();
  expect(screen.getByText("From 1 to 2.")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(onCancel).toHaveBeenCalledTimes(1);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(onCancel).toHaveBeenCalledTimes(2);
  expect(onConfirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Change it" }));
  expect(onConfirm).toHaveBeenCalledTimes(1);
});

it("disables the confirm button while it is busy", () => {
  render(
    <ConfirmDialog
      title="T"
      confirmLabel="Go"
      busy
      onConfirm={() => undefined}
      onCancel={() => undefined}
    >
      <p>x</p>
    </ConfirmDialog>,
  );
  expect(screen.getByRole("button", { name: "Go" })).toHaveProperty(
    "disabled",
    true,
  );
});

it("shows loading, empty and error states that say what to do next", () => {
  render(
    <>
      <Loading what="the rounds" />
      <Empty title="No rounds yet" next="Start a round to see it here." />
      <ErrorState message="The backend is down." next="Reload the page." />
    </>,
  );
  expect(screen.getByRole("status").textContent).toBe("Loading the rounds...");
  expect(screen.getByText("No rounds yet")).toBeDefined();
  expect(screen.getByText("Start a round to see it here.")).toBeDefined();
  expect(screen.getByRole("alert").textContent).toContain(
    "The backend is down.",
  );
  expect(screen.getByText("Reload the page.")).toBeDefined();
});
