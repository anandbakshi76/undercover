import { expect, test } from "@playwright/test";
import { signIn } from "./sign-in";

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test("the claim page shows the sections the agent read", async ({
  page,
  request,
}) => {
  for (const text of [
    "Policy wording 3. Trip Delay",
    "Claims manual 10. Meals without receipts",
  ]) {
    await request.post("/api/claims/CLM-1015/steps", {
      data: { kind: "section", text },
    });
  }
  await page.goto("/#/claims/CLM-1015");
  const read = page.getByRole("list", { name: "Sections read" });
  await expect(read.getByRole("listitem")).toHaveText([
    "Policy wording 3. Trip Delay",
    "Claims manual 10. Meals without receipts",
  ]);
});

test("a claim that fails twice needs a handler", async ({ page, request }) => {
  const started = await request.post("/api/claims/CLM-1016/start-run");
  const runId = ((await started.json()) as { run_id: string }).run_id;
  for (const problem of ["The model timed out.", "The provider failed."]) {
    await request.post("/api/claims/CLM-1016/start", {
      data: { run_id: runId },
    });
    await request.post("/api/claims/CLM-1016/release", { data: { problem } });
  }
  await request.post(`/api/runs/${runId}/finish`, {
    data: { status: "finished", summary: "CLM-1016 is left for a handler." },
  });

  await page.goto("/");
  const row = page.getByRole("row", { name: /CLM-1016/ });
  await expect(row.getByText("Needs a handler")).toBeVisible();
  await row.getByRole("link", { name: "CLM-1016" }).click();
  await expect(page.locator(".actions .badge")).toHaveText("Needs a handler");
  await expect(
    page.getByText("It is left for a handler.", { exact: false }).first(),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Run agent" })).toBeHidden();
  await expect(
    page.getByText(
      "The provider failed. It has failed twice and is left for a handler.",
    ),
  ).toBeVisible();
});

test("the instructions page shows the claim agent's instructions, read only", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Agent instructions" }).click();
  await expect(
    page
      .getByRole("region", { name: "Working a claim" })
      .getByText("You decide one travel insurance claim"),
  ).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
});
