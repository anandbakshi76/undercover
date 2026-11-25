import { expect, test } from "@playwright/test";
import { signIn } from "./sign-in";

const STAND_IN = "http://127.0.0.1:8003";

test.afterEach(async ({ request }) => {
  await request.post("/api/runs/stop-running");
});

test("a handler approves the improver's proposal on the page, and a handler without the right can't", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/rounds/create", {
    data: { started_by: "HND-01" },
  });
  const { round_id: roundId, stretch_id: stretchId } =
    (await created.json()) as {
      round_id: string;
      stretch_id: string;
    };
  const proposed = await request.post(
    `/api/rounds/${roundId}/change-proposals`,
    {
      data: {
        instructions: "Be brief.\nStop early.",
        model: "openai/gpt-6-luna",
        reasoning_effort: "medium",
        reason: "Shorter answers are cheaper.",
      },
    },
  );
  const proposalId = ((await proposed.json()) as { pending: { id: number } })
    .pending.id;
  await request.post(`/api/runs/${stretchId}/finish`, {
    data: { status: "finished" },
  });

  await signIn(page);
  await page.goto("/#/improvement");
  const card = page.getByRole("region", {
    name: "Proposed change waiting for a handler",
  });
  await expect(card).toContainText("Shorter answers are cheaper.");
  await expect(card).toContainText("low to medium");
  await expect(card.getByText("Be brief.")).toBeVisible();

  await card.getByRole("button", { name: "Approve" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Approve" })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const sent = (await (await request.get(`${STAND_IN}/requests`)).json()) as {
    path: string;
    body: Record<string, string>;
  }[];
  expect(sent.at(-1)).toEqual({
    method: "POST",
    path: `/api/proposals/${String(proposalId)}/approve`,
    body: { handler_id: "HND-01", round_id: roundId, reason: "" },
  });

  // The test plays the service: it records the decision, then ends the round.
  await request.post(`/api/proposals/${String(proposalId)}/decision`, {
    data: { decision: "approve", handler_id: "HND-01", reason: "" },
  });
  await page.reload();
  await expect(page.getByText("Evaluations (1 of 3)")).toBeVisible();
  const stretch = await request.post(`/api/rounds/${roundId}/stretches`);
  const next = ((await stretch.json()) as { stretch_id: string }).stretch_id;
  await request.post(`/api/rounds/${roundId}/finish`);
  await request.post(`/api/runs/${next}/finish`, {
    data: { status: "finished" },
  });
  await page.reload();
  await expect(page.getByText("Rolled back").first()).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Start a round" }),
  ).toBeEnabled();

  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "marcus");
  await page.goto("/#/improvement");
  await expect(
    page.getByText(/Only a handler who can sign off payouts/).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Start a round" }),
  ).toBeDisabled();
});
