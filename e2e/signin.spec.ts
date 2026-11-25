import { expect, test } from "@playwright/test";
import { HANDLERS, PASSWORD, signIn } from "./sign-in";

test("a visitor who has not signed in sees only the sign-in page", async ({
  page,
}) => {
  await page.goto("/#/claims/CLM-1001");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("navigation")).toHaveCount(0);
  await expect(page.getByText(/sign up|create an account/i)).toHaveCount(0);
});

test("a wrong password shows the message and stays on sign-in", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill(HANDLERS.dana);
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("Invalid login credentials");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});

test("a signed-in handler is named in the header and can sign out", async ({
  page,
}) => {
  await signIn(page, "marcus");
  const header = page.getByRole("banner");
  await expect(header.getByText("Marcus Oyelaran")).toBeVisible();
  await expect(
    header.getByText("Claims Handler", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});

test("a person who is not a handler is turned away", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill("stranger@example.com");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "Sign in as a claims handler to do this.",
  );
});

test("the backend refuses a handler action with no sign-in", async ({
  request,
}) => {
  const started = await request.post("/api/dispatches", {
    data: { concurrency: 3 },
  });
  expect(started.status()).toBe(401);
  const signedOff = await request.post("/api/claims/CLM-1012/sign-off");
  expect(signedOff.status()).toBe(401);
});

test("a handler who can't sign off is told why, and Dana can", async ({
  page,
  request,
}) => {
  const run = await request.post("/api/claims/CLM-1012/start-run");
  expect(run.ok()).toBe(true);
  await request.post("/api/claims/CLM-1012/decision", {
    data: {
      decision: "approve",
      payout: 1250,
      reason: "The wording covers it.",
      policy_section: "Section 5, Baggage",
    },
  });

  await signIn(page, "marcus");
  await page.goto("/#/claims/CLM-1012");
  await expect(
    page.getByText(
      "Marcus Oyelaran can't sign off this payout. Only a handler who can sign off payouts over the limit may.",
    ),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign off" })).toHaveCount(0);

  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "dana");
  await page.goto("/#/claims/CLM-1012");
  await expect(page.getByText("You are signing off as")).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign off" })).toBeVisible();
});
