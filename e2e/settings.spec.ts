import { expect, test } from "@playwright/test";
import { signIn } from "./sign-in";

test("a senior handler changes a setting through the dialog and it is logged", async ({
  page,
}) => {
  await signIn(page, "dana");
  await page.goto("/#/settings");
  await expect(
    page.getByRole("heading", { name: "Settings", exact: true }),
  ).toBeVisible();

  await page.getByLabel("Claim agents", { exact: true }).fill("10");
  await page
    .getByRole("button", {
      name: "Save claim agents at once in the improver's evaluations",
    })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("25");
  await expect(dialog).toContainText("10");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await page
    .getByRole("button", {
      name: "Save claim agents at once in the improver's evaluations",
    })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Change it" })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const changes = page.getByRole("region", { name: "Changes to the settings" });
  await expect(changes.getByText("Dana Whitfield")).toBeVisible();
  await expect(
    page.getByText("Last changed by Dana Whitfield", { exact: false }).first(),
  ).toBeVisible();
});

test("another handler sees the values and why they can't change them", async ({
  page,
  request,
}) => {
  await signIn(page, "marcus");
  await page.goto("/#/settings");
  await expect(
    page.getByText(
      "Marcus Oyelaran can't change the settings. Only a handler who can sign off payouts over the limit may.",
    ),
  ).toBeVisible();
  await expect(page.getByLabel("Accuracy floor")).toBeDisabled();
  await expect(page.getByRole("button", { name: /^Save / })).toHaveCount(0);
  const refused = await request.put("/api/settings/round_limit", {
    data: { value: 5 },
  });
  expect(refused.status()).toBe(401);
});

test("the models allowed are listed, and the appearance can be switched", async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: "light" });
  await signIn(page, "dana");
  await page.goto("/#/settings");
  const models = page.getByRole("region", { name: "Models allowed" });
  await expect(models.getByText("openai/gpt-6-luna")).toBeVisible();
  await expect(models.getByText("deepseek/deepseek-v4.1-flash")).toBeVisible();

  const background = () =>
    page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  await page.getByRole("radio", { name: "Dark" }).check();
  expect(await background()).toBe("rgb(4, 35, 46)");
  await page.reload();
  await expect(page.getByRole("radio", { name: "Dark" })).toBeChecked();
  expect(await background()).toBe("rgb(4, 35, 46)");
  await page.getByRole("radio", { name: "The same as the computer" }).check();
  expect(await background()).toBe("rgb(240, 244, 245)");
});
