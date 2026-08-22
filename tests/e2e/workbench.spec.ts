import { expect, request, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

const apiBase = `${(process.env.DEEBEE_API_URL || "http://127.0.0.1:8000/api").replace(/\/$/, "")}/`;
const database = "deebee_e2e";
const switchDatabase = process.env.DEEBEE_SWITCH_DATABASE || "mysql";
const table = "deebee_ui_replica_e2e";
const createdDatabase = "deebee_ui_created_e2e";
const runShortcut = process.platform === "darwin" ? "Meta+Enter" : "Control+Enter";
const alignmentTables = Array.from({ length: 12 }, (_, index) => `deebee_ui_align_${String(index).padStart(2, "0")}`);
let api: APIRequestContext;
let sessionId = "";

async function query(sql: string) {
  const response = await api.post(`sessions/${sessionId}/query`, { data: { sql, limit: 1000 } });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("用户名").fill("admin");
  await page.getByLabel("密码").fill("deebee");
  await page.getByRole("button", { name: "登录工作台" }).click();
  await expect(page.getByText("MySQL · root")).toBeVisible();
  await expect(page.locator(".monaco-editor textarea")).toBeVisible();
}

async function setSql(page: Page, sql: string) {
  const editor = page.locator(".monaco-editor textarea");
  await editor.focus();
  await page.keyboard.press(process.platform === "darwin" ? "Meta+A" : "Control+A");
  await page.keyboard.press("Backspace");
  await page.keyboard.insertText(sql);
  return editor;
}

function tableNode(page: Page): Locator {
  return page.locator(".object-node").filter({ hasText: table }).first();
}

test.beforeAll(async () => {
  let context = await request.newContext({ baseURL: apiBase });
  const auth = await context.post("auth/login", { data: { username: "admin", password: "deebee" } });
  const token = (await auth.json()).token;
  await context.dispose();
  context = await request.newContext({ baseURL: apiBase, extraHTTPHeaders: { Authorization: `Bearer ${token}` } });
  api = context;
  const session = await api.post("sessions", { data: { profile_id: "mysql-default", database, autocommit: true } });
  expect(session.ok(), await session.text()).toBeTruthy();
  const sessionPayload = await session.json();
  expect(sessionPayload.id).toBeTruthy();
  sessionId = sessionPayload.id;
  await query(`DROP DATABASE IF EXISTS \`${createdDatabase}\``);
  await query(`DROP TABLE IF EXISTS \`${table}\``);
  await query(`CREATE TABLE \`${table}\` (id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(80) NOT NULL, status VARCHAR(20) NOT NULL, amount DECIMAL(10,2), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)`);
  await query(`INSERT INTO \`${table}\` (name,status,amount) VALUES ('Alpha','active',12.50),('Beta','pending',23.75),('Gamma','active',9.90)`);
  for (const name of alignmentTables) {
    await query(`DROP TABLE IF EXISTS \`${name}\``);
    await query(`CREATE TABLE \`${name}\` (id INT PRIMARY KEY, value VARCHAR(20))`);
  }
});

test.afterAll(async () => {
  if (sessionId) {
    await query(`DROP DATABASE IF EXISTS \`${createdDatabase}\``);
    await query(`DROP TABLE IF EXISTS \`${table}\``);
    for (const name of alignmentTables) await query(`DROP TABLE IF EXISTS \`${name}\``);
    await api.delete(`sessions/${sessionId}`);
  }
  await api?.dispose();
});

test.describe.serial("DeeBee Vue MySQL workbench", () => {
  test("Vue/Iconify tree has reliable arrows and aligned icons with many tables", async ({ page }) => {
    await login(page);
    const server = page.locator(".server-node");
    await expect(server).toContainText("db01.dev.idmesh.cn:3306");
    await page.getByLabel(/收起服务器/).click();
    await expect(page.locator(".db-node")).toHaveCount(0);
    await page.getByLabel(/展开服务器/).click();
    await expect(page.locator(".db-node").filter({ hasText: database })).toBeVisible();
    await expect(page.locator(".titlebar")).toHaveCount(0);
    await page.getByRole("button", { name: `收起 ${database}` }).click();
    await expect(page.getByRole("button", { name: `展开 ${database}` })).toBeVisible();
    await page.getByRole("button", { name: `展开 ${database}` }).click();
    await expect(tableNode(page)).toBeVisible();
    await page.getByRole("button", { name: `收起 ${database}` }).click();
    await page.getByRole("button", { name: `展开 ${database}` }).click();
    await expect(tableNode(page)).toBeVisible();
    const icons = page.locator(".object-node svg.node-icon");
    expect(await icons.count()).toBeGreaterThanOrEqual(13);
    const positions = await icons.evaluateAll(nodes => nodes.slice(0, 13).map(node => Math.round(node.getBoundingClientRect().x)));
    expect(new Set(positions).size).toBe(1);
    await expect(page.locator(".toolbar svg").first()).toBeVisible();
    const toolbarButton = page.locator(".toolbar>button").first();
    const toolbarIconBox = await toolbarButton.locator("svg").boundingBox();
    const toolbarTextBox = await toolbarButton.locator("span").boundingBox();
    expect(toolbarIconBox!.x).toBeLessThan(toolbarTextBox!.x);
    const firstTab = page.locator(".tab").first();
    const tabBox = await firstTab.boundingBox();
    const closeBox = await firstTab.getByRole("button", { name: /关闭/ }).boundingBox();
    expect(Math.abs((tabBox!.x + tabBox!.width) - (closeBox!.x + closeBox!.width))).toBeLessThanOrEqual(10);
  });

  test("visible toolbar action creates a database through the real dialog", async ({ page }) => {
    await login(page);
    await page.locator(".toolbar").getByRole("button", { name: "新建数据库", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "新建数据库" });
    await dialog.getByLabel("数据库名称").fill(createdDatabase);
    await dialog.getByLabel("数据库字符集").selectOption("utf8mb4");
    await dialog.getByRole("button", { name: "创建", exact: true }).click();
    await expect(page.getByText(`数据库 ${createdDatabase} 已创建`)).toBeVisible();
    await expect(page.locator(".db-node").filter({ hasText: createdDatabase })).toBeVisible();
  });

  test("database and table context menus expose the P0-P2 command set", async ({ page }) => {
    await login(page);
    await page.locator(".db-node").filter({ hasText: database }).click({ button: "right" });
    const dbMenu = page.getByRole("menu", { name: `${database} 数据库操作` });
    for (const label of ["关闭数据库", "编辑数据库…", "新建数据库…", "删除数据库", "新建查询", "控制台", "执行 SQL 文件…", "转储 SQL 文件…", "打印数据库…", "在数据库中查找…", "刷新"]) await expect(dbMenu.getByRole("menuitem", { name: label })).toBeVisible();
    await page.keyboard.press("Escape");
    await tableNode(page).click({ button: "right" });
    const tableMenu = page.getByRole("menu", { name: `${table} 表操作` });
    for (const label of ["打开表", "设计表", "新建表", "删除表", "清空表", "Truncate 表", "复制表", "查看权限…", "导入向导…", "导出向导…", "数据生成…", "转储 SQL 文件…", "维护", "复制名称", "复制 DDL", "重命名…", "刷新"]) await expect(tableMenu.getByRole("menuitem", { name: label, exact: true })).toBeVisible();
  });

  test("changing the query database changes the real backend session", async ({ page }) => {
    await login(page);
    const selector = page.getByLabel("查询数据库");
    await selector.selectOption(switchDatabase);
    await expect(page.getByText(`查询标签已切换到 ${switchDatabase}`)).toBeVisible();
    let editor = await setSql(page, "SELECT DATABASE() AS current_database;");
    await editor.press(runShortcut);
    await expect(page.getByRole("cell", { name: switchDatabase, exact: true })).toBeVisible();
    await selector.selectOption(database);
    await expect(page.getByText(`查询标签已切换到 ${database}`)).toBeVisible();
    editor = await setSql(page, "SELECT DATABASE() AS current_database;");
    await editor.press(runShortcut);
    await expect(page.getByRole("cell", { name: database, exact: true })).toBeVisible();
  });

  test("lost backend query sessions are recreated and the SQL is safely retried", async ({ page }) => {
    const initialSession = page.waitForResponse(response => response.url().endsWith("/api/sessions") && response.request().method() === "POST");
    await login(page);
    const staleSessionId = (await (await initialSession).json()).id;
    const removed = await api.delete(`sessions/${staleSessionId}`);
    expect(removed.ok()).toBeTruthy();
    const editor = await setSql(page, "SELECT 77 AS reconnected;");
    await editor.press(runShortcut);
    await expect(page.getByText("查询会话已自动重连")).toBeVisible();
    await expect(page.getByRole("cell", { name: "77", exact: true })).toBeVisible();
  });

  test("network outage is shown and a manual health check restores the workspace", async ({ page }) => {
    await login(page);
    await page.route("**/api/**", route => route.abort("connectionfailed"));
    const editor = await setSql(page, "SELECT 88 AS after_network;");
    await editor.press(runShortcut);
    await expect(page.locator(".connection-state.offline")).toBeVisible();
    await expect(page.getByText("后台连接中断，正在自动重连。本条 SQL 未自动重放。")).toBeVisible();
    await page.unroute("**/api/**");
    await page.locator(".connection-state").click();
    await expect(page.locator(".connection-state.online")).toBeVisible();
    await expect(page.getByText("连接已恢复，本条 SQL 未自动重放，请重新执行。")).toBeVisible();
    await editor.press(runShortcut);
    await expect(page.getByRole("cell", { name: "88", exact: true })).toBeVisible();
  });

  test("query editor height supports drag, keyboard adjustment and persistence", async ({ page }) => {
    await login(page);
    const editor = page.locator(".editor-wrap");
    const splitter = page.getByRole("separator", { name: "调整 SQL 编辑区高度" });
    const before = await editor.boundingBox();
    const handle = await splitter.boundingBox();
    await page.mouse.move(handle!.x + handle!.width / 2, handle!.y + handle!.height / 2);
    await page.mouse.down();
    await page.mouse.move(handle!.x + handle!.width / 2, handle!.y + 68, { steps: 5 });
    await page.mouse.up();
    const dragged = await editor.boundingBox();
    expect(dragged!.height - before!.height).toBeGreaterThanOrEqual(55);
    await page.reload();
    await expect(page.locator(".monaco-editor textarea")).toBeVisible();
    const persisted = await editor.boundingBox();
    expect(Math.abs(persisted!.height - dragged!.height)).toBeLessThanOrEqual(2);
    await splitter.focus();
    await splitter.press("ArrowUp");
    const keyboardAdjusted = await editor.boundingBox();
    expect(persisted!.height - keyboardAdjusted!.height).toBeGreaterThanOrEqual(16);
    await splitter.dblclick();
    await expect(editor).toHaveCSS("height", "260px");
  });

  test("SQL intelligence completes real tables and alias-qualified columns", async ({ page }) => {
    await login(page);
    const editor = await setSql(page, "SELECT * FROM deebee_ui_rep");
    await editor.press("Control+Space");
    let suggestions = page.getByRole("listbox", { name: "Suggest" });
    const tableSuggestion = suggestions.getByRole("option", { name: new RegExp(`^${table}`) });
    await expect(tableSuggestion).toBeVisible();
    expect((await tableSuggestion.boundingBox())!.height).toBeGreaterThanOrEqual(28);
    await expect(tableSuggestion).toContainText("BASE TABLE");
    await editor.press("Escape");
    await setSql(page, `SELECT * FROM ${table} t WHERE t.`);
    await editor.press("Control+Space");
    suggestions = page.getByRole("listbox", { name: "Suggest" });
    for (const field of ["id", "name", "status", "amount", "created_at"]) await expect(suggestions.getByRole("option", { name: new RegExp(`^${field}`) })).toBeVisible();
    await editor.press("Escape");
    await setSql(page, `select id,name,status,amount from ${table} order by id limit 10;`);
    await page.locator(".query-bar").getByRole("button", { name: "格式化" }).click();
    await expect(page.locator(".view-lines")).toContainText("SELECT");
    await expect(page.locator(".view-lines")).toContainText("FROM");
    await editor.press(runShortcut);
    await expect(page.getByRole("button", { name: /结果 1 3/ })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Alpha", exact: true })).toBeVisible();
  });

  test("table data supports inline edit, persistent resizing, sorting and filtering", async ({ page }) => {
    await login(page);
    await tableNode(page).dblclick();
    await expect(page.getByText(`${database} · 双击单元格原地编辑`)).toHaveCount(0);
    await expect(page.locator(".data-view>.view-header")).toHaveCount(0);
    await expect(page.locator(".editable-grid-shell thead small")).toHaveCount(0);
    const nameHeader = page.getByRole("columnheader").filter({ hasText: "name" });
    const before = await nameHeader.boundingBox();
    const separator = page.getByRole("separator", { name: "调整 name 列宽" });
    await separator.focus();
    for (let index = 0; index < 4; index++) await separator.press("ArrowRight");
    const after = await nameHeader.boundingBox();
    expect(after!.width - before!.width).toBeGreaterThanOrEqual(60);
    await page.getByRole("cell", { name: "Alpha", exact: true }).dblclick();
    await page.getByLabel("编辑 name").fill("Alpha edited inline");
    await page.getByLabel("编辑 name").press("Enter");
    await expect(page.getByText("单元格修改已保存")).toBeVisible();
    await page.getByRole("button", { name: "刷新", exact: true }).last().click();
    await expect(page.getByRole("cell", { name: "Alpha edited inline", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "添加记录", exact: true }).click();
    await page.getByLabel("记录字段 name").fill("Browser inserted row");
    await page.getByLabel("记录字段 status").fill("active");
    await page.getByLabel("记录字段 amount").fill("31.25");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    const insertedCell = page.getByRole("cell", { name: "Browser inserted row", exact: true });
    await expect(insertedCell).toBeVisible();
    await page.getByRole("row").filter({ has: insertedCell }).click();
    const deleteButton = page.getByRole("button", { name: "删除", exact: true });
    await expect(deleteButton).toBeEnabled();
    page.once("dialog", dialog => dialog.accept());
    await deleteButton.click();
    await expect(page.getByRole("cell", { name: "Browser inserted row", exact: true })).toHaveCount(0);
    await page.getByLabel("筛选字段").selectOption("status");
    await page.getByLabel("筛选值").fill("pending");
    await page.getByRole("button", { name: "应用" }).click();
    await expect(page.getByRole("cell", { name: "Beta", exact: true })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Gamma", exact: true })).toHaveCount(0);
    await query(`UPDATE \`${table}\` SET name='Alpha' WHERE name='Alpha edited inline'`);
  });

  test("result grid actions, selection, sizing and frozen columns are wired", async ({ page }) => {
    await login(page);
    const editor = await setSql(page, `SELECT id,name,status,amount FROM ${table} ORDER BY id;`);
    await editor.press(runShortcut);
    await page.getByRole("cell", { name: "Alpha", exact: true }).click({ button: "right" });
    let menu = page.getByRole("menu", { name: "结果表格操作" });
    for (const label of ["复制单元格", "复制为", "全选", "取消全选", "设置当前列宽…", "自动调整全部列宽", "设置行高…", "冻结到当前列", "取消冻结所有列", "跳转到记录…"]) await expect(menu.getByRole("menuitem", { name: label, exact: true })).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "复制单元格", exact: true })).not.toHaveClass(/active/);
    await menu.getByRole("menuitem", { name: "复制为", exact: true }).hover();
    for (const label of ["INSERT Statement", "UPDATE Statement", "Tab Separated Values (Data only)", "Tab Separated Values (Field Name only)", "Tab Separated Values (Field Name and Data)", "JSON"]) await expect(page.getByRole("menuitem", { name: label, exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("cell", { name: "Alpha", exact: true }).click({ button: "right" });
    menu = page.getByRole("menu", { name: "结果表格操作" });
    await menu.getByRole("menuitem", { name: "全选", exact: true }).click();
    await expect(page.locator(".editable-grid-shell tbody tr.selected")).toHaveCount(3);
    await page.getByRole("cell", { name: "Alpha", exact: true }).click({ button: "right" });
    page.once("dialog", dialog => dialog.accept("24"));
    await page.getByRole("menuitem", { name: "设置当前列宽…", exact: true }).click();
    await expect.poll(async () => (await page.getByRole("columnheader").filter({ hasText: "name" }).boundingBox())?.width).toBeLessThan(80);
    for (const tabName of ["消息", "摘要", "Profile", "状态"]) {
      await page.locator(".result-tabs").getByRole("button", { name: tabName, exact: true }).click();
      await expect(page.locator(".result-tabs").getByRole("button", { name: tabName, exact: true })).toHaveClass(/current/);
    }
  });

  test("query tabs, history, saved queries and transaction controls work", async ({ page }) => {
    await login(page);
    await page.getByRole("button", { name: "新建查询标签" }).click();
    await expect(page.getByRole("tab")).toHaveCount(2);
    const editor = await setSql(page, "SELECT 42 AS answer;");
    await editor.press(runShortcut);
    await page.locator(".query-bar").getByRole("button", { name: "历史" }).click();
    await expect(page.getByText("SELECT 42 AS answer;", { exact: false })).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("tab", { selected: true }).click({ button: "right" });
    for (const label of ["关闭", "关闭其他标签", "关闭右侧标签", "复制标签", "保存查询…"]) await expect(page.getByRole("menuitem", { name: label, exact: true })).toBeVisible();
    page.once("dialog", dialog => dialog.accept("saved_e2e_query"));
    await page.getByRole("menuitem", { name: "保存查询…" }).click();
    await expect(page.getByText("查询 saved_e2e_query 已保存")).toBeVisible();
    await page.locator(".query-bar").getByRole("button", { name: "自动提交" }).click();
    await expect(page.getByText("已进入手动事务模式")).toBeVisible();
    await page.locator(".query-bar").getByRole("button", { name: "提交", exact: true }).click();
    await expect(page.getByText("事务已提交")).toBeVisible();
    await page.locator(".query-bar").getByRole("button", { name: "回滚", exact: true }).click();
    await expect(page.getByText("事务已回滚")).toBeVisible();
  });

  test("table designer and transfer wizards cover configuration and execution", async ({ page }) => {
    await login(page);
    await tableNode(page).click({ button: "right" });
    await page.getByRole("menuitem", { name: "设计表", exact: true }).click();
    for (const tabName of [/字段/, /索引/, /外键/, /约束/, /DDL 预览/]) await expect(page.locator(".designer-tabs").getByRole("button", { name: tabName })).toBeVisible();
    await expect(page.getByText("引擎")).toBeVisible();
    await page.getByRole("button", { name: "生成 DDL" }).click();
    await expect(page.getByText(/尚未生成 DDL|语句 1/)).toBeVisible();
    await page.getByRole("tab").first().click();
    for (const [menuName, title] of [["导入向导…", "数据导入向导"], ["导出向导…", "数据导出向导"], ["数据生成…", "测试数据生成器"], ["转储 SQL 文件…", "SQL 转储向导"]] as const) {
      await tableNode(page).click({ button: "right" });
      await page.getByRole("menuitem", { name: menuName, exact: true }).click();
      await expect(page.getByRole("dialog", { name: title })).toBeVisible();
      await page.getByRole("button", { name: "取消", exact: true }).click();
    }
    await tableNode(page).click({ button: "right" });
    await page.getByRole("menuitem", { name: "导入向导…", exact: true }).click();
    await page.locator(".file-drop input").setInputFiles({ name: "rows.csv", mimeType: "text/csv", buffer: Buffer.from("name,status,amount\nImported by browser,active,88.80\n") });
    await page.getByRole("button", { name: "下一步" }).click();
    await page.getByRole("button", { name: "开始执行" }).click();
    await expect(page.getByText(/成功导入 1 行/)).toBeVisible();
  });

  test("database SQL-file wizard executes a script and refreshes metadata", async ({ page }) => {
    await login(page);
    await page.locator(".db-node").filter({ hasText: database }).click({ button: "right" });
    await page.getByRole("menuitem", { name: "执行 SQL 文件…", exact: true }).click();
    await page.locator(".file-drop input").setInputFiles({ name: "script.sql", mimeType: "application/sql", buffer: Buffer.from(`INSERT INTO \`${table}\` (name,status,amount) VALUES ('SQL File','active',66.60);`) });
    await page.getByRole("button", { name: "下一步" }).click();
    await page.getByRole("button", { name: "开始执行" }).click();
    await expect(page.getByText(/SQL 文件执行完成/)).toBeVisible();
  });

  test("export, dump and generated-data jobs complete through their UI flows", async ({ page }) => {
    await login(page);
    await tableNode(page).click({ button: "right" });
    await page.getByRole("menuitem", { name: "导出向导…", exact: true }).click();
    await page.getByLabel("文件格式").selectOption("json");
    await page.getByRole("button", { name: "下一步" }).click();
    const exportDownload = page.waitForEvent("download");
    await page.getByRole("button", { name: "开始执行" }).click();
    await exportDownload;

    await tableNode(page).click({ button: "right" });
    await page.getByRole("menuitem", { name: "转储 SQL 文件…", exact: true }).click();
    await page.getByRole("button", { name: "下一步" }).click();
    const dumpDownload = page.waitForEvent("download");
    await page.getByRole("button", { name: "开始执行" }).click();
    await dumpDownload;

    await tableNode(page).click({ button: "right" });
    await page.getByRole("menuitem", { name: "数据生成…", exact: true }).click();
    await page.getByLabel("生成行数").fill("5");
    await page.getByRole("button", { name: "下一步" }).click();
    await page.getByRole("button", { name: "开始执行" }).click();
    await expect(page.getByText(/已生成 5 行数据/)).toBeVisible();
  });
});
