import { expect, type APIRequestContext, test } from "@playwright/test";
import { signIn } from "./sign-in";

interface Sent {
  path: string;
  body: Record<string, unknown> | null;
}

async function sentToAgent(request: APIRequestContext): Promise<Sent[]> {
  const sent = await request.get("http://127.0.0.1:8003/requests");
  return (await sent.json()) as Sent[];
}

async function approve(
  request: APIRequestContext,
  claimId: string,
  decision: string,
  payout: number,
) {
  await request.post(`/api/claims/${claimId}/decision`, {
    data: {
      decision,
      payout,
      reason: "The wording covers it.",
      policy_section: "Section 3, Trip Delay",
    },
  });
}

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test("starting a dispatch on the dashboard reaches the agent's service with N", async ({
  page,
  request,
}) => {
  await page.goto("/");
  const box = page.getByLabel("Claim agents at once");
  await expect(box).toHaveValue("3");
  await box.fill("7");
  await page.getByRole("button", { name: "Start a dispatch" }).click();
  await expect
    .poll(async () =>
      (await sentToAgent(request)).filter((r) => r.path === "/api/dispatches"),
    )
    .toEqual([
      { method: "POST", path: "/api/dispatches", body: { concurrency: 7 } },
    ]);
});

test("the queue shows a dispatch played out through the API", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/runs/dispatch", {
    data: {
      concurrency: 3,
      version: 1,
      model: "openai/gpt-6-luna",
      reasoning_effort: "low",
    },
  });
  const runId = ((await created.json()) as { run_id: string }).run_id;
  const refused = await request.post("/api/runs/dispatch", {
    data: {
      concurrency: 3,
      version: 1,
      model: "m",
      reasoning_effort: "low",
    },
  });
  expect(refused.status()).toBe(409);

  for (const claimId of ["CLM-1022", "CLM-1023", "CLM-1024"]) {
    await request.post(`/api/claims/${claimId}/start`, {
      data: { run_id: runId },
    });
    await request.post(`/api/claims/${claimId}/model-calls`, {
      data: {
        run_id: runId,
        prompt_tokens: 1000,
        completion_tokens: 200,
        cost: 0.0125,
      },
    });
    await request.post(`/api/claims/${claimId}/work-time`, {
      data: {
        run_id: runId,
        started_at: "2026-11-08T10:00:00.000+00:00",
        seconds: 4.5,
      },
    });
  }
  await approve(request, "CLM-1022", "approve", 300);
  await request.post("/api/claims/CLM-1022/payment", { data: { amount: 300 } });
  await approve(request, "CLM-1023", "decline", 0);
  await approve(request, "CLM-1024", "approve", 1250);

  await page.goto("/");
  const panel = page.getByRole("region", { name: "Current run" });
  await expect(panel.getByText("Running")).toBeVisible();
  await expect(panel.getByText("$0.0375")).toBeVisible();
  await expect(panel.getByRole("row", { name: /CLM-1022/ })).toContainText(
    "Decided",
  );
  await expect(
    page.getByRole("button", { name: "Start a dispatch" }),
  ).toBeDisabled();

  await request.post(`/api/runs/${runId}/finish`, {
    data: { status: "finished" },
  });
  await expect(panel.getByText("Finished")).toBeVisible();
  await expect(
    panel.getByText("2 approved, 1 declined and 0 referred.", { exact: false }),
  ).toBeVisible();
  await expect(
    panel.getByText("is waiting for a sign-off on $1,250.00."),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Start a dispatch" }),
  ).toBeEnabled();
});

test("starting an evaluation on the scoreboard reaches the service with the set, tiny and N", async ({
  page,
  request,
}) => {
  await page.goto("/#/scoreboard");
  await page.getByLabel("Set").selectOption("held_out");
  await page.getByLabel("Claims", { exact: true }).selectOption("tiny");
  await page.getByLabel("Claim agents at once").fill("5");
  await page.getByRole("button", { name: "Start an evaluation" }).click();
  await expect
    .poll(async () =>
      (await sentToAgent(request)).filter((r) => r.path === "/api/evaluations"),
    )
    .toEqual([
      {
        method: "POST",
        path: "/api/evaluations",
        body: { concurrency: 5, evaluation_set: "held_out", tiny: true },
      },
    ]);
});

test("the scoreboard shows an evaluation played out through the API", async ({
  page,
  request,
}) => {
  const queueBefore = (await (await request.get("/api/claims")).json()) as {
    claim_id: string;
  }[];
  const created = await request.post("/api/runs/evaluation", {
    data: {
      concurrency: 5,
      version: 1,
      round_id: null,
      evaluation_set: "held_out",
      tiny: true,
      model: "openai/gpt-6-luna",
      reasoning_effort: "low",
    },
  });
  const run = (await created.json()) as {
    run_id: string;
    claims_worked: string[];
  };
  expect(run.claims_worked).toHaveLength(5);

  const [first, second, third, fourth] = run.claims_worked;
  for (const copy of run.claims_worked) {
    await request.post(`/api/claims/${copy}/start`, {
      data: { run_id: run.run_id },
    });
    await request.post(`/api/claims/${copy}/model-calls`, {
      data: {
        run_id: run.run_id,
        prompt_tokens: 1000,
        completion_tokens: 200,
        cost: 0.02,
      },
    });
    await request.post(`/api/claims/${copy}/work-time`, {
      data: {
        run_id: run.run_id,
        started_at: "2026-11-08T10:00:00.000+00:00",
        seconds: 6,
      },
    });
  }
  await approve(request, first, "decline", 0);
  await approve(request, second, "refer", 0);
  await approve(request, third, "approve", 1250);
  await approve(request, fourth, "approve", 100);
  await request.post(`/api/runs/${run.run_id}/finish`, {
    data: { status: "finished" },
  });

  await page.goto("/#/scoreboard");
  const table = page.getByRole("table", { name: "Evaluations" });
  const row = table.getByRole("row", { name: /Held-out set/ }).first();
  await expect(row).toContainText("Finished");
  await expect(row).toContainText("$0.0200");
  await expect(row).toContainText("6.0 s");
  await expect(row).toContainText("1.68");
  await expect(row).toContainText("%");
  await row.getByRole("link").click();

  const measures = page.getByLabel("Measures");
  await expect(measures).toContainText("Grade");
  await expect(measures).toContainText("1.68");
  await expect(measures).toContainText("Judge cost");
  await expect(measures).toContainText("Decision accuracy");
  await expect(measures).toContainText("Cost of mistakes");
  await expect(measures).toContainText("Straight through");
  const claimsTable = page.getByRole("table", { name: "Evaluation claims" });
  await expect(claimsTable.getByRole("row")).toHaveCount(6);
  const graded = claimsTable.getByRole("row").nth(1);
  await expect(graded).toContainText("B");
  await expect(graded).toContainText("2.10");
  await expect(graded).toContainText("Yes");
  await expect(graded).toContainText("No");
  await expect(claimsTable.getByRole("row").nth(5)).toContainText("0.00");
  await expect(
    claimsTable.getByText("Match").or(claimsTable.getByText("Miss")).first(),
  ).toBeVisible();

  await claimsTable.getByRole("row").nth(3).getByRole("link").click();
  await expect(page.getByText(/worked for an evaluation/)).toBeVisible();
  await expect(page.locator(".actions .badge")).toHaveText(
    "Waiting for sign-off",
  );
  await expect(page.getByLabel("Handler")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Sign off/ })).toHaveCount(0);

  const queueAfter = (await (await request.get("/api/claims")).json()) as {
    claim_id: string;
  }[];
  expect(queueAfter).toHaveLength(queueBefore.length);
  expect(queueAfter.every((c) => /^CLM-\d+$/.test(c.claim_id))).toBe(true);
  await page.goto("/");
  await expect(
    page.getByRole("list", { name: "Claims by state" }),
  ).toBeVisible();
});
