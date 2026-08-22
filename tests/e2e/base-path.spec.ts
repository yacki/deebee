import { expect, test } from "@playwright/test";

test("login API follows the page deployment path", async ({ page }) => {
  await page.goto("/deebee/");

  const loginRequest = page.waitForRequest(request =>
    request.method() === "POST" && request.url().endsWith("/auth/login")
  );
  await page.getByLabel("用户名").fill("path-check");
  await page.getByLabel("密码").fill("path-check");
  await page.getByRole("button", { name: "登录工作台" }).click();

  const request = await loginRequest;
  expect(new URL(request.url()).pathname).toBe("/deebee/api/auth/login");
});
