import { expect, type Page, test } from "@playwright/test";

// F6. Needs the live stack and an Admin created with create_initial_admin.
const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL ?? "admin@example.com";
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "";
const EMPLOYEE_EMAIL = `e2e.${Date.now()}@example.com`;
const EMPLOYEE_PASSWORD = "Ledger-E2E-Employee-2026";

async function signIn(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

test("Admin creates an Employee who then sees limited navigation", async ({ page }) => {
  test.skip(!ADMIN_PASSWORD, "Set E2E_ADMIN_PASSWORD to run against a live stack.");

  await signIn(page, ADMIN_EMAIL, ADMIN_PASSWORD);
  await page.getByRole("link", { name: "Users" }).click();
  await page.getByRole("button", { name: "Add user" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Email").fill(EMPLOYEE_EMAIL);
  await dialog.getByLabel("First name").fill("E2E");
  await dialog.getByLabel("Employee", { exact: true }).check();
  await dialog.getByLabel("Password").fill(EMPLOYEE_PASSWORD);
  await dialog.getByRole("button", { name: "Add user" }).click();
  await expect(page.getByText(EMPLOYEE_EMAIL)).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();

  await signIn(page, EMPLOYEE_EMAIL, EMPLOYEE_PASSWORD);
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link")).toHaveText(["Home", "My profile"]);
  await page.goto("/admin/users");
  await expect(page.getByRole("heading", { name: "You don't have access to this page" })).toBeVisible();
});
