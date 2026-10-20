import { test, expect, type Page, type APIRequestContext } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';

const configPath = process.env.DEEBEE_APPROVAL_QA_CONFIG || '';
test.skip(!configPath, 'Requires an explicitly configured isolated approval lab');
// The private file contains only disposable acceptance credentials and IDs.
const config = configPath ? JSON.parse(readFileSync(configPath, 'utf8')) : {};
const base: string = config.base || '';
test.describe.configure({mode: 'serial'});
test.use({timezoneId: 'Australia/Perth', viewport: {width: 1440, height: 1050}});
let admin: APIRequestContext, adminPage: Page, userPage: Page;
let csrf = '', writer = '';
const issues: string[] = [];

async function choose(page: Page, label: string, text: string) {
  await page.getByRole('combobox', {name: label, exact: true}).click();
  await page.locator('.arco-select-dropdown:visible').last().getByText(text, {exact: true}).click();
  await page.keyboard.press('Escape');
}
async function api(path: string, method = 'GET', data?: unknown) {
  const response = await admin.fetch(base + '/api/admin/v1/' + path, {method, data});
  expect(response.ok(), await response.text()).toBeTruthy(); return response.json();
}
async function own(id: string) {
  const response = await userPage.request.get(base + '/api/v1/executions/' + id);
  expect(response.ok(), await response.text()).toBeTruthy(); return response.json();
}
async function query(sql: string) {
  const submitted = await userPage.request.post(base + '/api/v1/db/queries', {headers: {'X-CSRF-Token': csrf}, data: {resource_id: config.db_resource, sql, idempotency_key: randomUUID()}});
  expect(submitted.status(), await submitted.text()).toBe(202);
  const id = (await submitted.json()).id;
  await expect.poll(async () => (await own(id)).status).toBe('succeeded');
  return (await own(id)).result;
}
async function submit(resource: 'db' | 'ssh', command: string, reason: string) {
  await userPage.locator('.access-resource-cards button').filter({hasText: resource === 'db' ? '审批验收 · MySQL 专用库' : '审批验收 · SSH 测试目录'}).click();
  if (resource === 'db') {
    await choose(userPage, '资源账号', writer + ' · 高权限账号 · 直接授权');
    await choose(userPage, '操作', '修改数据库');
  } else await choose(userPage, '操作', '执行服务器命令');
  await userPage.getByLabel(resource === 'db' ? 'SQL' : '服务器命令', {exact: true}).fill(command);
  await userPage.getByLabel('申请原因', {exact: true}).fill(reason);
  await userPage.getByRole('button', {name: '提交执行', exact: true}).click();
  await expect(userPage.getByText('申请已提交，原命令尚未执行。', {exact: false})).toBeVisible();
  const response = await userPage.request.get(base + '/api/v1/executions');
  const record = (await response.json()).items.find((r: {approval?: {reason: string}}) => r.approval?.reason === reason);
  expect(record.status).toBe('pending_approval'); return record.id as string;
}
async function review(id: string, command: string, approve: boolean) {
  await adminPage.getByRole('navigation').getByText('操作审批', {exact: true}).click();
  await adminPage.getByRole('button', {name: '查询申请', exact: true}).click();
  const record = await api('executions/' + id);
  const row = adminPage.getByRole('row').filter({hasText: record.approval.reason});
  await expect(row).toHaveCount(1);
  await row.getByRole('button', {name: '查看并审核', exact: true}).click();
  await expect(adminPage.locator('.approval-command')).toHaveText(command);
  await adminPage.getByLabel('审核意见', {exact: true}).fill(approve ? '已核对，操作仅影响专用验收数据。' : '拒绝本次测试，验证远端数据保持不变。');
  await adminPage.getByRole('button', {name: approve ? '通过并执行' : '拒绝申请', exact: true}).click();
  await expect.poll(async () => (await own(id)).status).toBe(approve ? 'succeeded' : 'rejected');
  await adminPage.getByRole('button', {name: '刷新审批与结果', exact: true}).click();
  await expect(adminPage.locator('.approval-detail')).toContainText(approve ? '成功' : '已拒绝');
  await adminPage.locator('.arco-modal:visible .arco-modal-close-btn').click();
  await expect.poll(async () => userPage.locator('.access-user-main').innerText()).toContain(approve ? '成功' : '已拒绝');
}

test.beforeAll(async ({browser, playwright}) => {
  expect(base).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/deebee$/);
  admin = await playwright.request.newContext();
  const login = await admin.post(base + '/api/auth/login', {data: {username: config.admin_username, password: config.admin_password}});
  expect(login.ok()).toBeTruthy(); const token = (await login.json()).token;
  await admin.dispose(); admin = await playwright.request.newContext({extraHTTPHeaders: {Authorization: 'Bearer ' + token}});
  writer = (await api('accounts')).items.find((a: {resource_id: string; tier: string}) => a.resource_id === config.db_resource && a.tier === 'privileged').username;
  const adminContext = await browser.newContext();
  adminPage = await adminContext.newPage(); adminPage.on('pageerror', e => issues.push(e.message));
  await adminPage.addInitScript(token => sessionStorage.setItem('deebee_token', token), token);
  await adminPage.goto(base + '/admin');
  await expect(adminPage.getByRole('heading', {name: '资源管理', exact: true})).toBeVisible();
  const userContext = await browser.newContext();
  userPage = await userContext.newPage(); userPage.on('pageerror', e => issues.push(e.message));
  await userPage.goto(base + '/#access-user');
  await userPage.getByLabel('用户名', {exact: true}).fill(config.username);
  await userPage.getByLabel('密码', {exact: true}).fill(config.password);
  await userPage.getByRole('button', {name: '登录', exact: true}).click();
  await expect(userPage.getByRole('heading', {name: '危险操作审批体验账号 的可用资源', exact: true})).toBeVisible();
  csrf = (await (await userPage.request.get(base + '/api/v1/me')).json()).csrf;
});
test.afterAll(async () => { await adminPage?.context().close(); await userPage?.context().close(); await admin?.dispose(); });

test('普通账号提交 DELETE，管理员核对原始 SQL 后通过才改变数据库', async () => {
  expect((await query('SELECT COUNT(*) FROM items WHERE id=1')).rows[0][0]).toBe(1);
  const command = 'DELETE FROM items WHERE id=1';
  const id = await submit('db', command, '浏览器验收：删除专用库 items 的一条测试数据');
  expect((await query('SELECT COUNT(*) FROM items WHERE id=1')).rows[0][0]).toBe(1);
  await review(id, command, true);
  expect((await query('SELECT COUNT(*) FROM items WHERE id=1')).rows[0][0]).toBe(0);
  await userPage.screenshot({path: 'test-results/approval-user-database.png', fullPage: true});
});

test('DROP TABLE 拒绝保留原表，通过只删除专用测试表', async () => {
  const rejected = await submit('db', 'DROP TABLE reject_target', '浏览器验收：拒绝删除 reject_target');
  await review(rejected, 'DROP TABLE reject_target', false);
  expect((await query('SELECT COUNT(*) FROM reject_target')).rows[0][0]).toBe(2);
  const approved = await submit('db', 'DROP TABLE drop_target', '浏览器验收：通过删除 drop_target');
  await review(approved, 'DROP TABLE drop_target', true);
  const schema = await userPage.request.get(base + '/api/v1/resources/' + config.db_resource + '/schema');
  expect((await schema.json()).columns.some((c: {table: string}) => c.table === 'drop_target')).toBe(false);
});

test('SSH 删除目录分别通过和拒绝，普通用户无法打开原始交互终端绕过审批', async () => {
  const command = 'rm -rf ' + config.remote_dir + '/approved';
  const approved = await submit('ssh', command, '浏览器验收：删除新建 SSH 目录 approved');
  await review(approved, command, true);
  const rejectedCommand = 'rm -rf ' + config.remote_dir + '/rejected';
  const rejected = await submit('ssh', rejectedCommand, '浏览器验收：拒绝删除新建 SSH 目录 rejected');
  await review(rejected, rejectedCommand, false);
  const response = await userPage.request.post(base + '/api/v1/sessions', {headers: {'X-CSRF-Token': csrf}, data: {resource_id: config.ssh_resource}});
  expect(response.status()).toBe(403);
  expect((await response.json()).error.code).toBe('APPROVAL_COMMAND_MODE_REQUIRED');
});

test('规则新增、协议与资源范围、停用、编辑、删除和从模板新增', async () => {
  await adminPage.getByRole('navigation').getByText('操作审批', {exact: true}).click();
  await adminPage.getByRole('button', {name: '审批规则与模板', exact: true}).click();
  await expect(adminPage.getByRole('row').filter({hasText: '其他 Shell 命令（保守兜底）'})).toHaveCount(1);
  await adminPage.getByRole('button', {name: '新增审批规则', exact: true}).click();
  await adminPage.getByLabel('审批规则名称', {exact: true}).fill('验收自定义只读查询规则');
  await choose(adminPage, '审批协议', '数据库（MySQL / PostgreSQL）');
  await choose(adminPage, '审批匹配方式', '包含指定文本');
  await adminPage.getByLabel('审批匹配文本', {exact: true}).fill('select 42');
  await choose(adminPage, '审批适用资源', '审批验收 · MySQL 专用库');
  await adminPage.getByRole('button', {name: '保存审批规则', exact: true}).click();
  await expect(adminPage.locator('.approval-form')).toHaveCount(0);
  const row = adminPage.getByRole('row').filter({hasText: '验收自定义只读查询规则'});
  await expect(row).toContainText('审批验收 · MySQL 专用库');
  const r = await userPage.request.post(base + '/api/v1/db/queries', {headers: {'X-CSRF-Token': csrf}, data: {resource_id: config.db_resource, sql: 'SELECT 42', idempotency_key: randomUUID()}});
  expect((await r.json()).status).toBe('pending_approval');
  await row.getByRole('button', {name: '编辑规则', exact: true}).click();
  await adminPage.getByLabel('审批规则说明', {exact: true}).fill('浏览器已验证自定义匹配与资源范围');
  await adminPage.getByRole('button', {name: '保存审批规则', exact: true}).click();
  await row.getByRole('button', {name: '停用规则', exact: true}).click();
  await expect(row).toContainText('已停用');
  expect((await query('SELECT 42')).rows[0][0]).toBe(42);
  await row.getByRole('button', {name: '删除规则', exact: true}).click(); await expect(row).toHaveCount(0);
  await choose(adminPage, '危险命令模板', '无 WHERE 的更新和删除');
  await adminPage.getByRole('button', {name: '从模板新增', exact: true}).click();
  await adminPage.getByLabel('审批规则名称', {exact: true}).fill('验收模板副本');
  await adminPage.getByRole('button', {name: '保存审批规则', exact: true}).click();
  const copied = adminPage.getByRole('row').filter({hasText: '验收模板副本'});
  await expect(copied).toContainText('内置检测');
  await copied.getByRole('button', {name: '删除规则', exact: true}).click();
  await expect(copied).toHaveCount(0);
  await adminPage.locator('.access-main').evaluate(el => {el.scrollTop = 0;});
  await adminPage.screenshot({path: 'test-results/approval-rules.png', fullPage: true});
  expect(issues).toEqual([]);
});
