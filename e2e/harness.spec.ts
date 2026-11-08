import { expect, type APIRequestContext, test } from "@playwright/test";

async function startRun(request: APIRequestContext, limit: number) {
  const run = await request.post("/api/runs", { data: { limit } });
  return ((await run.json()) as { run_id: string }).run_id;
}

function todos(...items: [string, string][]) {
  return {
    todos: items.map(([content, status]) => ({ content, status })),
  };
}

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
  const runId = await startRun(request, 1);
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

test("the queue shows the run's to-do list being ticked off", async ({
  page,
  request,
}) => {
  const runId = await startRun(request, 2);
  await request.put(`/api/runs/${runId}/todos`, {
    data: todos(["Work CLM-1020", "to_do"], ["Work CLM-1021", "to_do"]),
  });
  await page.goto("/");
  const run = page.getByRole("region", { name: "Current run" });
  const list = run.getByRole("list", { name: "To-do list" });
  await expect(run.getByText("Running")).toBeVisible();
  await expect(list.getByRole("listitem")).toHaveText([
    "To doWork CLM-1020",
    "To doWork CLM-1021",
  ]);

  await request.put(`/api/runs/${runId}/todos`, {
    data: todos(["Work CLM-1020", "done"], ["Work CLM-1021", "in_progress"]),
  });
  await expect(list.getByRole("listitem")).toHaveText([
    "DoneWork CLM-1020",
    "In progressWork CLM-1021",
  ]);

  await request.put(`/api/runs/${runId}/todos`, {
    data: todos(["Work CLM-1020", "done"], ["Work CLM-1021", "done"]),
  });
  await request.post(`/api/runs/${runId}/finish`, {
    data: { status: "finished", summary: "Worked 2 claims, both approved." },
  });
  await expect(run.getByText("Worked 2 claims, both approved.")).toBeVisible();
  await expect(run.getByText("Finished")).toBeVisible();
  await expect(list.getByRole("listitem")).toHaveText([
    "DoneWork CLM-1020",
    "DoneWork CLM-1021",
  ]);
});

test("the instructions page shows both sets, read only", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Agent instructions" }).click();
  await expect(
    page
      .getByRole("region", { name: "Running the queue" })
      .getByText("You are the UnderCover Agent, running the claims queue"),
  ).toBeVisible();
  await expect(
    page
      .getByRole("region", { name: "Working a claim" })
      .getByText("You decide one travel insurance claim"),
  ).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
});
