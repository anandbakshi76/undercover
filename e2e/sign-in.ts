import { expect, type Page } from "@playwright/test";

export const HANDLERS = {
  dana: "dana.whitfield@undercover.example",
  marcus: "marcus.oyelaran@undercover.example",
  priya: "priya.raman@undercover.example",
};

export const PASSWORD = "e2e-password";

export async function signIn(page: Page, who: keyof typeof HANDLERS = "dana") {
  await page.goto("/");
  await page.getByLabel("Email address").fill(HANDLERS[who]);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}
