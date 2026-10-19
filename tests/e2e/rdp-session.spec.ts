import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";

const profile = {
  id: "rdp-test", driver: "rdp", name: "Windows", host: "rdp.example.test", port: 3389, user: "operator",
  default_database: "", default_schema: "",
  options: { domain: "", security: "any", ignore_certificate: false, color_depth: 24, timezone: "Australia/Perth" },
};
const instruction = (...values: string[]) => values.map(value => `${Array.from(value).length}.${value}`).join(",") + ";";

async function mockRdp(page: Page, connect: (socket: WebSocketRoute, attempt: number) => void) {
  let attempts = 0;
  let savedProfile = { ...profile };
  await page.clock.install();
  await page.addInitScript(() => sessionStorage.setItem("deebee_token", "test-token"));
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "PATCH") {
      savedProfile = { ...savedProfile, ...route.request().postDataJSON() };
      return route.fulfill({ json: savedProfile });
    }
    return route.fulfill({ json: path.endsWith("/connections") ? [savedProfile] : path.endsWith("/jobs") ? [] : { ok: true, latency_ms: 5 } });
  });
  await page.routeWebSocket("**/api/connections/*/rdp*", socket => {
    attempts += 1;
    socket.onMessage(() => {});
    socket.send(instruction("", `connection-${attempts}`));
    socket.send(instruction("size", "0", "800", "600"));
    socket.send(instruction("sync", "100"));
    connect(socket, attempts);
  });
  await page.goto("/");
  await page.getByRole("button", { name: "打开会话", exact: true }).click();
  await page.clock.runFor(100);
  return () => attempts;
}

for (const failure of [
  { message: "RDP 服务器证书校验失败，请核对并信任服务器证书后重试", code: "519" },
  { message: "RDP 登录失败，请检查用户名、密码和域", code: "769" },
]) {
  test(`RDP preserves ${failure.code} errors and stays offline despite healthy backend checks`, async ({ page }) => {
    const attempts = await mockRdp(page, socket => socket.send(instruction("error", failure.message, failure.code)));
    await expect(page.locator(".rdp-session .remote-toolbar")).toContainText(failure.message);
    await expect(page.locator(".connection-state.offline")).toBeVisible();
    await page.clock.runFor(20_000);
    expect(attempts()).toBe(1);
    await page.locator(".connection-state").click();
    await expect(page.locator(".connection-state.offline")).toBeVisible();
    await expect(page.getByText(/当前会话已断开，请点击/)).toBeVisible();
    await expect(page.locator(".rdp-session .remote-dot.error")).toBeVisible();
    await expect(page.locator(".rdp-session .remote-toolbar")).toContainText(failure.message);
    await page.getByRole("button", { name: "重新连接", exact: true }).click();
    await expect.poll(attempts).toBe(2);
  });
}

test("RDP waits for a new desktop frame after the initial placeholder", async ({ page }) => {
  let desktop: WebSocketRoute | undefined;
  await mockRdp(page, socket => { desktop = socket; });
  await expect(page.locator(".rdp-session .remote-dot.connecting")).toBeVisible();
  await expect(page.locator(".connection-state.checking")).toBeVisible();
  desktop!.send(instruction("sync", "100"));
  await page.clock.runFor(1000);
  await expect(page.locator(".rdp-session .remote-dot.connecting")).toBeVisible();
  desktop!.send(instruction("sync", "123"));
  await page.clock.runFor(100);
  await expect(page.locator(".rdp-session .remote-dot.connected")).toBeVisible();
  await expect(page.locator(".connection-state.online")).toBeVisible();
});

test("RDP automatically reconnects transient failures and confirms a new desktop frame", async ({ page }) => {
  const attempts = await mockRdp(page, (socket, attempt) => {
    socket.send(attempt === 1 ? instruction("error", "Server unavailable", "520") : instruction("sync", "123"));
  });
  await expect(page.locator(".rdp-session .remote-dot.error")).toBeVisible();
  await page.clock.runFor(1500);
  await expect.poll(attempts).toBe(2);
  await page.clock.runFor(100);
  await expect(page.locator(".rdp-session .remote-toolbar")).toContainText("远程桌面已连接");
  await expect(page.locator(".connection-state.online")).toBeVisible();
});

test("RDP bounds automatic retries when the upstream remains unavailable", async ({ page }) => {
  const attempts = await mockRdp(page, socket => socket.send(instruction("error", "Server unavailable", "520")));
  await expect(page.locator(".rdp-session .remote-dot.error")).toBeVisible();
  await page.clock.runFor(15_000);
  expect(attempts()).toBe(4);
  await page.clock.runFor(60_000);
  expect(attempts()).toBe(4);
  await expect(page.locator(".connection-state.offline")).toBeVisible();
});

test("saving edited RDP credentials opens a fresh desktop session", async ({ page }) => {
  const attempts = await mockRdp(page, socket => socket.send(instruction("sync", "123")));
  await expect(page.locator(".rdp-session .remote-dot.connected")).toBeVisible();
  await page.getByRole("button", { name: "连接设置", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "编辑服务器连接" });
  await dialog.getByLabel("连接密码", { exact: true }).fill("updated-test-password");
  await dialog.getByRole("button", { name: "保存并连接" }).click();
  await expect(dialog).not.toBeVisible();
  await expect.poll(attempts).toBe(2);
  await expect(page.getByRole("tab", { name: /远程桌面 2/ })).toHaveAttribute("aria-selected", "true");
});
