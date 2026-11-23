import { expect, type APIRequestContext, test } from "@playwright/test";

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
      model: "openai/gpt-6-luna",
      reasoning_effort: "low",
    },
  });
  const runId = ((await created.json()) as { run_id: string }).run_id;
  const refused = await request.post("/api/runs/dispatch", {
    data: { concurrency: 3, model: "m", reasoning_effort: "low" },
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
