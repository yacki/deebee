import { expect, test, type Page } from "@playwright/test";
import type { ConnectionDriver, ConnectionOptions } from "../../src/types";

type ConnectionRequest = {
  driver: ConnectionDriver; name: string; host: string; port: number; user: string;
  password: string | null; private_key: string | null; options: Partial<ConnectionOptions>; profile_id?: string;
};
type MockProfile = Omit<ConnectionRequest, "password" | "private_key"> & { id: string };
const fingerprint = "SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG";
const sshProfile: MockProfile = {
  id: "ssh-saved", driver: "ssh", name: "Saved SSH", host: "ssh.example.test", port: 22, user: "root",
  options: { auth_method: "password", strict_host_key: true, host_key_fingerprint: "" },
};

async function mockWorkbench(page: Page, initial: MockProfile[] = []) {
  let profiles = structuredClone(initial);
  const tested: ConnectionRequest[] = [];
  const saved: ConnectionRequest[] = [];
  const sockets: string[] = [];
  await page.addInitScript(() => sessionStorage.setItem("deebee_token", "test-token"));
  await page.routeWebSocket("**/api/connections/*/ssh*", socket => {
    sockets.push(socket.url());
    socket.onMessage(() => {});
    socket.send(JSON.stringify({ type: "state", state: "connected" }));
  });
  await page.route("**/api/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/connections/test")) {
      const body = request.postDataJSON() as ConnectionRequest;
      tested.push(body);
      const expected = body.driver === "ssh" ? body.options.host_key_fingerprint : body.options.ssh_host_key_fingerprint;
      await route.fulfill({ json: { latency_ms: 5, host_key_new: !expected, host_key_fingerprint: expected || fingerprint } });
    } else if (path.endsWith("/connections") && request.method() === "GET") {
      await route.fulfill({ json: profiles });
    } else if (request.method() === "PATCH" || (path.endsWith("/connections") && request.method() === "POST")) {
      const body = request.postDataJSON() as ConnectionRequest;
      saved.push(body);
      const id = request.method() === "PATCH" ? path.split("/").at(-1)! : "ssh-new";
      const profile: MockProfile = { id, driver:body.driver, name:body.name, host:body.host, port:body.port, user:body.user, options:body.options };
      profiles = [...profiles.filter(item => item.id !== id), profile];
      await route.fulfill({ json: profile });
    } else if (path.endsWith("/jobs") || path.endsWith("/databases")) {
      await route.fulfill({ json: [] });
    } else {
      await route.fulfill({ json: { ok: true, latency_ms: 5 } });
    }
  });
  return { tested, saved, sockets };
}

async function newSshConnection(page: Page) {
  await page.goto("/");
  const dialog = page.getByRole("dialog", { name: "新建服务器连接" });
  await dialog.getByRole("button", { name: "SSH", exact: true }).click();
  await dialog.getByLabel("服务器主机").fill("ssh.example.test");
  await dialog.getByLabel("连接密码").fill("test-password");
  return dialog;
}

test("saving a first SSH connection requires visible trust before persistence or a session", async ({ page }) => {
  const mock = await mockWorkbench(page);
  const dialog = await newSshConnection(page);
  await dialog.getByRole("button", { name: "保存并连接" }).click();
  const confirmation = dialog.getByRole("group", { name: "确认 SSH 主机密钥" });
  await expect(confirmation).toContainText(fingerprint);
  await expect(confirmation).toContainText("ssh.example.test:22");
  await expect(dialog.getByRole("button", { name: "保存并连接" })).toBeDisabled();
  expect(mock.saved).toHaveLength(0);
  expect(mock.sockets).toHaveLength(0);
  await confirmation.getByRole("button", { name: "信任并保存连接" }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator(".ssh-session")).toBeVisible();
  expect(mock.saved).toHaveLength(1);
  expect(mock.saved[0].options.host_key_fingerprint).toBe(fingerprint);
  await expect.poll(() => mock.sockets.length).toBe(1);
});

test("testing exposes an inline trust action and cancellation keeps the connection unsaved", async ({ page }) => {
  const mock = await mockWorkbench(page);
  const dialog = await newSshConnection(page);
  await dialog.getByRole("button", { name: "测试连接", exact: true }).click();
  await dialog.getByRole("button", { name: "取消信任" }).click();
  await expect(dialog).toContainText("已取消信任 SSH 主机密钥，未保存连接");
  expect(mock.saved).toHaveLength(0);
  expect(mock.sockets).toHaveLength(0);
  await dialog.getByRole("button", { name: "测试连接", exact: true }).click();
  await dialog.getByRole("button", { name: "信任此主机" }).click();
  await expect(dialog).toContainText("已确认 SSH 主机密钥");
  expect(mock.saved).toHaveLength(0);
  await dialog.getByRole("button", { name: "保存并连接" }).click();
  await expect(dialog).not.toBeVisible();
  expect(mock.saved[0].options.host_key_fingerprint).toBe(fingerprint);
});

test("an existing connection missing a fingerprint opens confirmation and preserves stored credentials", async ({ page }) => {
  const mock = await mockWorkbench(page, [sshProfile]);
  await page.goto("/");
  const dialog = page.getByRole("dialog", { name: "编辑服务器连接" });
  await expect(dialog.getByRole("group", { name: "确认 SSH 主机密钥" })).toContainText(fingerprint);
  expect(mock.tested[0]).toMatchObject({ profile_id: sshProfile.id, password: null, private_key: null });
  expect(mock.saved).toHaveLength(0);
  expect(mock.sockets).toHaveLength(0);
  await dialog.getByRole("button", { name: "信任并保存连接" }).click();
  await expect(dialog).not.toBeVisible();
  expect(mock.saved[0]).toMatchObject({ password: null, private_key: null, options: { host_key_fingerprint: fingerprint } });
  await expect.poll(() => mock.sockets.length).toBe(1);
});

test("opening an untrusted saved SSH session after dismissing settings asks for trust again", async ({ page }) => {
  const mock = await mockWorkbench(page, [sshProfile]);
  await page.goto("/");
  const dialog = page.getByRole("dialog", { name: "编辑服务器连接" });
  await expect(dialog.getByRole("group", { name: "确认 SSH 主机密钥" })).toBeVisible();
  await dialog.getByRole("button", { name: "关闭连接设置" }).click();
  await page.getByRole("button", { name: "打开会话", exact: true }).click();
  await expect(dialog.getByRole("group", { name: "确认 SSH 主机密钥" })).toBeVisible();
  expect(mock.sockets).toHaveLength(0);
  expect(mock.saved).toHaveLength(0);
});

test("changing host or port discards pending and previously confirmed SSH fingerprints", async ({ page }) => {
  const mock = await mockWorkbench(page);
  const dialog = await newSshConnection(page);
  const confirmation = dialog.getByRole("group", { name: "确认 SSH 主机密钥" });
  await dialog.getByRole("button", { name: "测试连接", exact: true }).click();
  await expect(confirmation).toBeVisible();
  await dialog.getByLabel("服务器主机").fill("other.example.test");
  await expect(confirmation).not.toBeVisible();
  await dialog.getByRole("button", { name: "测试连接", exact: true }).click();
  await expect(confirmation).toContainText("other.example.test:22");
  await dialog.getByRole("button", { name: "信任此主机" }).click();
  await dialog.getByLabel("服务器端口").fill("2222");
  await dialog.getByRole("button", { name: "保存并连接" }).click();
  await expect(confirmation).toContainText("other.example.test:2222");
  expect(mock.tested.at(-1)?.options.host_key_fingerprint).toBe("");
  expect(mock.saved).toHaveLength(0);
});

test("a late test response cannot attach a fingerprint to a different SSH host", async ({ page }) => {
  const mock = await mockWorkbench(page);
  const dialog = await newSshConnection(page);
  let release!: () => void;
  const released = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/connections/test", async route => {
    await released;
    await route.fulfill({ json: { latency_ms: 5, host_key_new: true, host_key_fingerprint: fingerprint } });
  });
  await dialog.getByRole("button", { name: "保存并连接" }).click();
  await expect(dialog.getByRole("button", { name: "正在测试…" })).toBeVisible();
  await dialog.getByLabel("服务器主机").fill("other.example.test");
  release();
  await expect(dialog.getByRole("button", { name: "测试连接", exact: true })).toBeEnabled();
  await expect(dialog.getByRole("group", { name: "确认 SSH 主机密钥" })).not.toBeVisible();
  expect(mock.saved).toHaveLength(0);
});

test("failed SSH tests leave an actionable error and never offer trust or save", async ({ page }) => {
  const mock = await mockWorkbench(page);
  const dialog = await newSshConnection(page);
  await page.route("**/api/connections/test", route => route.fulfill({ status: 400, json: { error: { message: "SSH 连接失败：认证失败" } } }));
  await dialog.getByRole("button", { name: "保存并连接" }).click();
  await expect(dialog).toContainText("SSH 连接失败：认证失败");
  await expect(dialog.getByRole("group", { name: "确认 SSH 主机密钥" })).not.toBeVisible();
  expect(mock.saved).toHaveLength(0);
});

test("a saved trusted SSH connection opens without a second trust prompt", async ({ page }) => {
  const mock = await mockWorkbench(page, [{ ...sshProfile, options: { ...sshProfile.options, host_key_fingerprint: fingerprint } }]);
  await page.goto("/");
  await page.getByRole("button", { name: "打开会话", exact: true }).click();
  await expect(page.locator(".ssh-session")).toBeVisible();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  expect(mock.tested).toHaveLength(0);
  expect(mock.saved).toHaveLength(0);
});

test("saving an edited SSH connection opens a terminal with the saved configuration", async ({ page }) => {
  const mock = await mockWorkbench(page, [{ ...sshProfile, options: { ...sshProfile.options, host_key_fingerprint: fingerprint } }]);
  await page.goto("/");
  await page.getByRole("button", { name: "打开会话", exact: true }).click();
  await expect.poll(() => mock.sockets.length).toBe(1);
  await page.getByRole("button", { name: "连接设置", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "编辑服务器连接" });
  await dialog.getByLabel("服务器主机").fill("other.example.test");
  await dialog.getByRole("button", { name: "保存并连接" }).click();
  await expect(dialog.getByRole("group", { name: "确认 SSH 主机密钥" })).toContainText("other.example.test:22");
  expect(mock.sockets).toHaveLength(1);
  await dialog.getByRole("button", { name: "信任并保存连接" }).click();
  await expect(dialog).not.toBeVisible();
  expect(mock.saved[0]).toMatchObject({ host: "other.example.test", options: { host_key_fingerprint: fingerprint } });
  await expect.poll(() => mock.sockets.length).toBe(2);
  await expect(page.getByRole("tab", { name: /SSH 终端 2/ })).toHaveAttribute("aria-selected", "true");
});

test("cancelling trust after save-and-connect leaves no connection or terminal", async ({ page }) => {
  const mock = await mockWorkbench(page);
  const dialog = await newSshConnection(page);
  await dialog.getByRole("button", { name: "保存并连接" }).click();
  await dialog.getByRole("button", { name: "取消信任" }).click();
  await expect(dialog.getByRole("group", { name: "确认 SSH 主机密钥" })).not.toBeVisible();
  expect(mock.saved).toHaveLength(0);
  expect(mock.sockets).toHaveLength(0);
  await dialog.getByRole("button", { name: "保存并连接" }).click();
  await dialog.getByRole("button", { name: "信任并保存连接" }).click();
  await expect.poll(() => mock.sockets.length).toBe(1);
  expect(mock.saved).toHaveLength(1);
});

test("private-key SSH authentication follows the same save-and-connect confirmation", async ({ page }) => {
  const mock = await mockWorkbench(page);
  const dialog = await newSshConnection(page);
  await dialog.getByRole("button", { name: "选项", exact: true }).click();
  await dialog.getByLabel("SSH 认证方式", { exact: true }).selectOption("private_key");
  await dialog.getByLabel("SSH 私钥", { exact: true }).fill("test-private-key");
  await dialog.getByRole("button", { name: "保存并连接" }).click();
  await expect(dialog.getByRole("group", { name: "确认 SSH 主机密钥" })).toBeVisible();
  expect(mock.saved).toHaveLength(0);
  await dialog.getByRole("button", { name: "信任并保存连接" }).click();
  await expect.poll(() => mock.sockets.length).toBe(1);
  expect(mock.saved[0]).toMatchObject({ private_key: "test-private-key", options: { auth_method: "private_key", host_key_fingerprint: fingerprint } });
});

test("a missing fingerprint in a test response cannot save an untrusted SSH connection", async ({ page }) => {
  const mock = await mockWorkbench(page);
  const dialog = await newSshConnection(page);
  await page.route("**/api/connections/test", route => route.fulfill({ json: { latency_ms: 5 } }));
  await dialog.getByRole("button", { name: "保存并连接" }).click();
  await expect(dialog).toContainText("SSH 测试未返回主机密钥指纹");
  await expect(dialog.getByRole("group", { name: "确认 SSH 主机密钥" })).not.toBeVisible();
  expect(mock.saved).toHaveLength(0);
  expect(mock.sockets).toHaveLength(0);
});

test("the toolbar test also exposes trust for a saved SSH connection", async ({ page }) => {
  const mock = await mockWorkbench(page, [sshProfile]);
  await page.goto("/");
  const dialog = page.getByRole("dialog", { name: "编辑服务器连接" });
  await expect(dialog.getByRole("group", { name: "确认 SSH 主机密钥" })).toBeVisible();
  await dialog.getByRole("button", { name: "关闭连接设置" }).click();
  await page.getByRole("button", { name: "测试连接", exact: true }).click();
  await expect(dialog.getByRole("group", { name: "确认 SSH 主机密钥" })).toContainText(fingerprint);
  await expect(dialog.getByRole("button", { name: "信任此主机" })).toBeVisible();
  expect(mock.saved).toHaveLength(0);
});

test("database SSH tunnels use the same inline fingerprint confirmation", async ({ page }) => {
  const mock = await mockWorkbench(page);
  await page.goto("/");
  const dialog = page.getByRole("dialog", { name: "新建服务器连接" });
  await dialog.getByRole("button", { name: "SSH 隧道", exact: true }).click();
  await dialog.getByLabel("通过 SSH 隧道连接", { exact: true }).check();
  await dialog.getByLabel("SSH 隧道主机", { exact: true }).fill("bastion.example.test");
  await dialog.getByLabel("SSH 隧道用户名", { exact: true }).fill("operator");
  await dialog.getByRole("button", { name: "保存并连接" }).click();
  const confirmation = dialog.getByRole("group", { name: "确认 SSH 主机密钥" });
  await expect(confirmation).toContainText("bastion.example.test:22");
  expect(mock.saved).toHaveLength(0);
  await confirmation.getByRole("button", { name: "信任并保存连接" }).click();
  await expect(dialog).not.toBeVisible();
  expect(mock.saved[0].options.ssh_host_key_fingerprint).toBe(fingerprint);
});
