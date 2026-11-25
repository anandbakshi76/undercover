import { expect, test } from "@playwright/test";
import { signIn } from "./sign-in";

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test("the queue shows every claim and opens one", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Claims queue" }),
  ).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(25);
  await page.getByRole("link", { name: "CLM-1001" }).click();
  await expect(
    page.getByRole("heading", { name: /CLM-1001 Flight home canceled/ }),
  ).toBeVisible();
  await expect(page.getByText("Hotel Aurelia Fiumicino.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Run agent" })).toBeVisible();
});

test("the agent's steps and decision show on the claim", async ({
  page,
  request,
}) => {
  for (const [kind, text] of [
    ["tool_call", 'get_claim({"claim_id": "CLM-1003"})'],
    ["reply", "Declined under section 7."],
  ]) {
    await request.post("/api/claims/CLM-1003/steps", { data: { kind, text } });
  }
  await page.goto("/#/claims/CLM-1003");
  const steps = page.getByRole("list", { name: "Agent steps" });
  await expect(steps.getByRole("listitem")).toHaveCount(2);
  await expect(steps).toContainText("Declined under section 7.");

  await request.post("/api/claims/CLM-1003/decision", {
    data: {
      decision: "decline",
      payout: 0,
      reason: "The treatment could have waited until the customer was home.",
      policy_section: "Section 7, Emergency Medical",
    },
  });
  await page.reload();
  await expect(page.locator(".chip")).toHaveText("Declined");
  await expect(
    page.getByText(
      "The treatment could have waited until the customer was home.",
    ),
  ).toBeVisible();
  await expect(
    page.getByText("Based on: Section 7, Emergency Medical"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Run agent" })).toBeHidden();
});

test("a sign-off puts the claim back in progress and tells the agent", async ({
  page,
  request,
}) => {
  await request.post("/api/claims/CLM-1011/decision", {
    data: {
      decision: "approve",
      payout: 1250,
      reason: "The airline lost the suitcase. The items are covered.",
      policy_section: "Section 5, Baggage and Personal Effects",
    },
  });
  const payment = await request.post("/api/claims/CLM-1011/payment", {
    data: { amount: 1250 },
  });
  expect(payment.status()).toBe(409);

  await page.goto("/#/claims/CLM-1011");
  await expect(page.locator(".actions .badge")).toHaveText(
    "Waiting for sign-off",
  );
  await expect(
    page.getByText("The payment waits for a handler's sign-off."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Sign off", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Sign off" })
    .click();
  await expect(page.locator(".actions .badge")).toHaveText("In progress");
  await expect(
    page.getByText(/Payment pending, signed off by Dana Whitfield at /),
  ).toBeVisible();

  await expect
    .poll(async () => {
      const sent = await request.get("http://127.0.0.1:8003/requests");
      return ((await sent.json()) as { path: string }[]).map((r) => r.path);
    })
    .toContain("/api/claims/CLM-1011/resume");

  // The agent is stood in for: it pays once the sign-off has reached it.
  const paid = await request.post("/api/claims/CLM-1011/payment", {
    data: { amount: 1250 },
  });
  expect(paid.status()).toBe(200);
  await expect(
    page.getByText(/Paid \$1,250\.00 on .*, signed off by Dana Whitfield at /),
  ).toBeVisible();
  await expect(page.locator(".actions .badge")).toHaveText("Decided");
});

test("dark mode switches the colors and is remembered", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  const background = () =>
    page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(await background()).toBe("rgb(240, 244, 245)");
  await page.getByRole("button", { name: "Dark mode" }).click();
  expect(await background()).toBe("rgb(4, 35, 46)");
  await page.reload();
  await expect(page.getByRole("button", { name: "Dark mode" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(await background()).toBe("rgb(4, 35, 46)");
});
