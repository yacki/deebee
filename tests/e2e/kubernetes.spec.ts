import fs from "node:fs";
import { expect, request, test } from "@playwright/test";

const kubeconfigPath = process.env.DEEBEE_KUBECONFIG;
const containerLabel = process.env.DEEBEE_K8S_CONTAINER;
const apiBase = `${(process.env.DEEBEE_API_URL || "http://127.0.0.1:8000/api").replace(/\/$/, "")}/`;

test.describe("Kubernetes workspace", () => {
  test.skip(!kubeconfigPath, "DEEBEE_KUBECONFIG is required for the live Kubernetes test");

  test("imports kubeconfig, browses resources and opens an audited cluster terminal", async ({ page }) => {
    const anonymous = await request.newContext({ baseURL: apiBase });
    const login = await anonymous.post("auth/login", { data: { username: "admin", password: "deebee" } });
    expect(login.ok(), await login.text()).toBeTruthy();
    const token = (await login.json()).token;
    await anonymous.dispose();

    const api = await request.newContext({
      baseURL: apiBase,
      extraHTTPHeaders: { Authorization: `Bearer ${token}` },
    });
    const existing = await api.get("connections");
    const profiles = await existing.json();
    let profile = profiles.find((item: { name: string }) => item.name === "Kubernetes E2E");
    if (!profile) {
      const created = await api.post("connections", {
        data: {
          driver: "k8s",
          name: "Kubernetes E2E",
          host: "127.0.0.1",
          port: 6443,
          user: "",
          password: "",
          private_key: fs.readFileSync(kubeconfigPath!, "utf8"),
          ssh_password: "",
          ssh_private_key: "",
          proxy_password: "",
          default_database: "",
          default_schema: "",
          options: { k8s_auth_method: "kubeconfig", namespace: "", verify_tls: true },
        },
      });
      expect(created.ok(), await created.text()).toBeTruthy();
      profile = await created.json();
    }
    const tested = await api.post(`connections/${profile.id}/test`);
    expect(tested.ok(), await tested.text()).toBeTruthy();
    await api.dispose();

    await page.goto("/");
    await page.getByLabel("用户名").fill("admin");
    await page.getByLabel("密码").fill("deebee");
    await page.getByRole("button", { name: "登录工作台" }).click();
    await page.getByRole("button", { name: "打开会话" }).click();
    await expect(page.locator(".k8s-workspace")).toBeVisible();
    await expect(page.locator(".k8s-row.depth-one").first()).toBeVisible();
    await expect(page.locator(".k8s-row.depth-one")).not.toHaveCount(0);

    await page.getByTitle("集群终端").click();
    const terminal = page.locator(".k8s-terminal:visible .xterm-helper-textarea");
    await expect(terminal).toBeVisible();
    await terminal.focus();
    await page.keyboard.type("printf 'DEEBEE_BROWSER_K8S_OK\\n'");
    await page.keyboard.press("Enter");
    await expect(page.locator(".k8s-terminal:visible .xterm-rows")).toContainText("DEEBEE_BROWSER_K8S_OK");

    if (containerLabel) {
      await page.getByTitle("搜索容器").click();
      await page.getByPlaceholder("筛选容器").fill(containerLabel);
      await page.locator(`.k8s-row.search-result[title="${containerLabel}"]`).click();
      const containerTerminal = page.locator(".k8s-terminal:visible .xterm-helper-textarea");
      await expect(containerTerminal).toBeVisible();
      await containerTerminal.focus();
      await page.keyboard.type("printf 'DEEBEE_BROWSER_CONTAINER_OK\\n'");
      await page.keyboard.press("Enter");
      await expect(page.locator(".k8s-terminal:visible .xterm-rows")).toContainText("DEEBEE_BROWSER_CONTAINER_OK");
    }
  });
});
