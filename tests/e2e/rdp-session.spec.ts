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

test("RDP decodes streamed PNG frames with Windows audio and cursor initialization", async ({ page }) => {
  const png = 'iVBORw0KGgoAAAANSUhEUgAAAAsAAAAQCAYAAADAvYV+AAAABmJLR0QA/wD/AP+gvaeTAAAAYklEQVQokY2RQQ4AIQgDW+L/v9y9qCEsIJ4QZggoJAnDYwAwFQwASI4EO8FEMH95CRYTnfCDOyGFK6GEM6GFo7AqKI4sSSsCJH1X+roFkKdjueABX/On77lz2uGtr6pj9okfTeJQAYVaxnMAAAAASUVORK5CYII=';
  await mockRdp(page, socket => {
    for (const values of [
      ['audio', '1', 'audio/L16;rate=44100,channels=2'],
      ['size', '-1', '11', '16'], ['img', '3', '12', '-1', 'image/png', '0', '0'],
      ['blob', '3', png], ['end', '3'], ['cursor', '0', '0', '-1', '0', '0', '11', '16'],
      ['img', '3', '14', '0', 'image/png', '0', '0'], ['blob', '3', png], ['end', '3'], ['sync', '123'],
    ]) socket.send(instruction(...values));
  });
  await expect(page.locator('.rdp-session .remote-dot.connected')).toBeVisible();
  await expect.poll(() => page.locator('.rdp-session canvas').evaluateAll(nodes => nodes.some(node => {
    const canvas = node as HTMLCanvasElement;
    return canvas.width >= 800 && canvas.height >= 600 && canvas.getContext('2d')!.getImageData(0, 0, 11, 16).data.some(value => value > 0);
  }))).toBe(true);
});

test("RDP opens once and never authenticates automatically on page reload or health checks", async ({ page }) => {
  let tests = 0;
  page.on("request", request => { if (new URL(request.url()).pathname.endsWith("/connections/rdp-test/test")) tests += 1; });
  const attempts = await mockRdp(page, socket => socket.send(instruction("sync", "123")));
  await expect(page.locator(".rdp-session .remote-dot.connected")).toBeVisible();
  expect(tests).toBe(0);
  expect(attempts()).toBe(1);
  await page.reload();
  await expect(page.getByRole("button", { name: "打开会话", exact: true })).toBeVisible();
  await page.clock.runFor(45_000);
  expect(tests).toBe(0);
  expect(attempts()).toBe(1);
  await expect(page.locator(".connection-state.offline")).toBeVisible();
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

for (const failure of [
  { message: "RDP 登录失败，请检查用户名、密码和域", code: "769", attempts: 1 },
  { message: "RDP 服务器证书校验失败", code: "519", attempts: 1 },
  { message: "Server unavailable", code: "520", attempts: 4 },
]) {
  test(`switching RDP tabs does not restart blocked ${failure.code} authentication or exhausted retries`, async ({ page }) => {
    const attempts = await mockRdp(page, socket => socket.send(instruction("error", failure.message, failure.code)));
    await page.clock.runFor(15_000);
    expect(attempts()).toBe(failure.attempts);
    await page.getByRole("button", { name: "新建远程会话", exact: true }).click();
    await page.clock.runFor(15_000);
    expect(attempts()).toBe(failure.attempts * 2);
    await page.getByRole("tab", { name: "Windows 关闭 Windows", exact: true }).click();
    await page.clock.runFor(15_000);
    expect(attempts()).toBe(failure.attempts * 2);
    await expect(page.locator(".rdp-session:visible .remote-toolbar")).toContainText(failure.message);
    await page.locator(".rdp-session:visible").getByRole("button", { name: "重新连接", exact: true }).click();
    await page.clock.runFor(15_000);
    expect(attempts()).toBe(failure.attempts * 3);
  });
}

test("saving edited RDP credentials opens a fresh desktop session", async ({ page }) => {
  const attempts = await mockRdp(page, socket => socket.send(instruction("sync", "123")));
  await expect(page.locator(".rdp-session .remote-dot.connected")).toBeVisible();
  await page.getByRole("button", { name: "连接设置", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "编辑服务器连接" });
  await dialog.getByLabel("连接密码", { exact: true }).fill("updated-test-password");
  await dialog.getByRole("button", { name: "保存并连接" }).click();
  await expect(dialog).not.toBeVisible();
  await expect.poll(attempts).toBe(2);
  await expect(page.getByRole("tab", { name: /Windows \(2\)/ })).toHaveAttribute("aria-selected", "true");
});
