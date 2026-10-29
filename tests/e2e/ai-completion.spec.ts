import { expect, test, type Page } from '@playwright/test';

const input = (page: Page) => page.locator('.monaco-editor textarea:visible');
const ghost = (page: Page) => page.locator('.view-line').filter({ has: page.locator('[class*=ghost-text-decoration]') });
const catalog = { database: 'demo', tables: [{ name: 'customers', object_type: 'BASE TABLE', columns: [{ name: 'id', data_type: 'int', nullable: false }] }], routines: [] };
const profile = { id: 'ai-test', name: 'AI 续写测试', driver: 'mysql', host: 'database.invalid', port: 3306, user: 'reader', default_database: 'demo', default_schema: '', options: {} };
type State = { calls: Array<Record<string, unknown>>; generations: Array<Record<string, unknown>>; errors: string[]; blocked: string[]; completion: string; generated: { code: string; message: string }; delay: number; enabled: boolean; failure: boolean };

async function setup(page: Page, enabled = true) {
  const state: State = { calls: [], generations: [], errors: [], blocked: [], completion: ' WHERE id > 10', generated: { code: 'SELECT id FROM customers ORDER BY id DESC LIMIT 10;', message: '' }, delay: 0, enabled, failure: false };
  page.on('pageerror', error => state.errors.push(error.message));
  await page.addInitScript(() => sessionStorage.setItem('deebee_token', 'isolated-ai-test'));
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let json: unknown = { ok: true };
    if (path.endsWith('/ai/status')) json = { enabled: state.enabled, share_schema: false };
    else if (path.endsWith('/ai/generate')) {
      state.generations.push(request.postDataJSON());
      if (state.delay) await new Promise(resolve => setTimeout(resolve, state.delay));
      if (state.failure) return route.fulfill({ status: 504, json: { error: { message: 'AI 响应超时' } } });
      json = state.generated;
    }
    else if (path.endsWith('/ai/completions')) {
      state.calls.push(request.postDataJSON());
      const completion = state.completion;
      if (state.delay) await new Promise(resolve => setTimeout(resolve, state.delay));
      if (state.failure) return route.fulfill({ status: 504, json: { error: { message: 'AI 响应超时，可继续输入或稍后重试' } } });
      json = { completion, reason: 'ok' };
    }
    else if (path.endsWith('/connections')) json = [profile];
    else if (path.endsWith('/databases')) json = [{ name: 'demo', charset: 'utf8mb4', collation: '' }];
    else if (path.endsWith('/objects')) json = { tables: catalog.tables, views: [], routines: [], triggers: [], events: [] };
    else if (path.endsWith('/catalog')) json = catalog;
    else if (path.endsWith('/sessions')) json = { id: 'isolated-session' };
    else if (path.endsWith('/jobs')) json = [];
    else if (request.method() !== 'GET' && !path.endsWith('/sessions/cleanup') && !/\/sessions\/[^/]+$/.test(path)) {
      state.blocked.push(path);
      return route.fulfill({ status: 403, json: { detail: 'Execution forbidden in AI tests' } });
    }
    return route.fulfill({ json });
  });
  await page.goto('/');
  await expect(input(page)).toBeVisible();
  return state;
}

async function typeSql(page: Page, sql = 'SELECT * FROM customers') {
  await input(page).focus();
  await input(page).press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
  await page.keyboard.insertText(sql);
}
async function savedSql(page: Page) {
  return page.evaluate(() => {
    const saved = JSON.parse(localStorage.getItem('deebee_workspace:ai-test') || '{}');
    return saved.tabs?.find((tab: { id: string }) => tab.id === saved.activeTabId)?.sql;
  });
}

test('AI ghost text accepts with Tab, stays editable and never executes', async ({ page }, info) => {
  const state = await setup(page);
  await expect(page.getByRole('button', { name: '✦ AI 续写', exact: true })).toBeVisible();
  await typeSql(page);
  await expect(ghost(page)).toContainText('WHERE id > 10');
  expect(state.calls.at(-1)?.schema_context).toBe('');
  expect(await savedSql(page)).toBe('SELECT * FROM customers');
  await page.screenshot({ path: info.outputPath('ai-ghost-text.png') });
  await input(page).press('Tab');
  await expect.poll(() => savedSql(page)).toBe('SELECT * FROM customers WHERE id > 10');
  expect(state.blocked).toEqual([]);
  expect(state.errors).toEqual([]);
});

test('Esc dismisses, pause prevents requests, disabled AI preserves ordinary completion', async ({ page }) => {
  const state = await setup(page);
  await typeSql(page);
  await expect(ghost(page)).toContainText('WHERE id > 10');
  await input(page).press('Escape');
  await expect(ghost(page)).toHaveCount(0);
  expect(await savedSql(page)).toBe('SELECT * FROM customers');
  await page.getByRole('button', { name: '✦ AI 续写', exact: true }).click();
  const before = state.calls.length;
  await typeSql(page, 'SELECT * FROM customers WHERE ');
  await page.waitForTimeout(1200);
  expect(state.calls.length).toBe(before);
  await input(page).press('Control+Space');
  await expect(page.locator('.suggest-widget.visible')).toContainText('id');
  expect(state.errors).toEqual([]);
});

test('old delayed response cannot overwrite newer input', async ({ page }) => {
  const state = await setup(page);
  state.delay = 1200;
  await typeSql(page);
  await expect.poll(() => state.calls.length).toBeGreaterThan(0);
  await input(page).press('Escape');
  state.completion = ' LIMIT 5';
  await typeSql(page, 'SELECT id FROM customers');
  await expect(ghost(page)).toContainText('LIMIT 5');
  await expect(ghost(page)).not.toContainText('WHERE');
  expect(await savedSql(page)).toBe('SELECT id FROM customers');
  expect(state.errors).toEqual([]);
});

test('provider timeout shows quiet feedback and typing remains available', async ({ page }) => {
  const state = await setup(page);
  state.failure = true;
  await typeSql(page);
  await expect(page.locator('.ai-editor-status')).toContainText('AI 响应超时');
  await typeSql(page, 'SELECT 42');
  await expect.poll(() => savedSql(page)).toBe('SELECT 42');
  expect(state.blocked).toEqual([]);
  expect(state.errors).toEqual([]);
});

test('disabled AI never sends command content', async ({ page }) => {
  const state = await setup(page, false);
  await typeSql(page);
  await page.waitForTimeout(1200);
  expect(state.calls).toEqual([]);
  await expect(page.locator('.ai-editor-status')).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test('admin can test, save and reload AI configuration without key echo', async ({ page }, info) => {
  const errors: string[] = [], writes: Array<Record<string, unknown>> = [];
  page.on('pageerror', error => errors.push(error.message));
  let settings = { enabled: false, share_schema: false, base_url: '', model: '', has_api_key: false, timeout_seconds: 10 };
  await page.addInitScript(() => sessionStorage.setItem('deebee_token', 'isolated-admin-test'));
  await page.route('**/api/**', route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let json: unknown = { items: [], sources: [] };
    if (path.endsWith('/ai/settings')) {
      if (request.method() === 'PUT') {
        const body = request.postDataJSON(); writes.push(body);
        settings = { enabled: body.enabled, share_schema: body.share_schema, base_url: body.base_url, model: body.model, timeout_seconds: body.timeout_seconds, has_api_key: true };
      }
      json = settings;
    } else if (path.endsWith('/ai/test')) json = { ok: true, message: '模型连接成功，可以保存并启用 AI 续写' };
    return route.fulfill({ json });
  });
  await page.goto('/admin');
  await page.getByRole('navigation', { name: '管理后台导航' }).getByText('AI 配置', { exact: true }).click();
  await page.getByLabel('模型服务地址', { exact: true }).fill('https://model.example/v1');
  await page.getByLabel('模型名称', { exact: true }).fill('fast-code-model');
  await page.getByLabel('AI API Key', { exact: true }).fill('fake-key-only');
  await page.getByText('启用 AI 命令续写', { exact: true }).click();
  await page.getByRole('button', { name: '测试连接', exact: true }).click();
  await expect(page.getByText('模型连接成功，可以保存并启用 AI 续写', { exact: true })).toBeVisible();
  expect(writes).toEqual([]);
  await page.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(page.getByLabel('AI API Key')).toHaveValue('');
  expect(writes[0]).toMatchObject({ enabled: true, api_key: 'fake-key-only', share_schema: false });
  await page.screenshot({ path: info.outputPath('ai-admin-settings.png') });
  await page.reload();
  await page.getByRole('navigation', { name: '管理后台导航' }).getByText('AI 配置', { exact: true }).click();
  await expect(page.getByLabel('模型名称')).toHaveValue('fast-code-model');
  await expect(page.getByLabel('AI API Key')).toHaveValue('');
  expect(errors).toEqual([]);
});

test('ordinary user gets shell continuation with session CSRF and no execution', async ({ page }) => {
  const calls: Array<{ body: Record<string, unknown>; headers: Record<string, string> }> = [], blocked: string[] = [], errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  // A leftover administrator token must never be used on the user surface.
  await page.addInitScript(() => sessionStorage.setItem('deebee_token', 'unused-admin-token'));
  await page.route('**/api/**', route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let json: unknown = { sources: [], items: [] };
    if (path.endsWith('/v1/me')) json = { csrf: 'test-csrf', principal: { name: '测试用户' }, roles: [] };
    else if (path.endsWith('/v1/resources')) json = { resources: [{ id: 'ssh-resource', name: '测试服务器', type: 'ssh', default_mode: 'normal', access_modes: [{ access_option_id: 'option', mode: 'normal', username: 'reader', actions: ['ssh.exec'] }] }] };
    else if (path.endsWith('/v1/ai/status')) json = { enabled: true, share_schema: false };
    else if (path.endsWith('/v1/ai/completions')) {
      calls.push({ body: request.postDataJSON(), headers: request.headers() });
      json = { completion: 'status --short', reason: 'ok' };
    } else if (path.endsWith('/v1/ai/generate')) {
      calls.push({ body: request.postDataJSON(), headers: request.headers() });
      json = { code: 'find . -type f -size +100M', message: '' };
    } else if (request.method() !== 'GET') { blocked.push(path); return route.fulfill({ status: 403, json: {} }); }
    return route.fulfill({ json });
  });
  await page.goto('/#access-user');
  await page.getByRole('button', { name: /测试服务器/ }).click();
  await expect(page.getByRole('button', { name: '✦ AI 续写', exact: true })).toBeVisible();
  await typeSql(page, 'git ');
  await expect(ghost(page)).toContainText('status --short');
  expect(calls.at(-1)?.body).toMatchObject({ resource_id: 'ssh-resource', language: 'shell' });
  expect(calls.at(-1)?.headers['x-csrf-token']).toBe('test-csrf');
  expect(calls.at(-1)?.headers.authorization).toBeUndefined();
  await input(page).press('Tab');
  await expect(input(page)).toHaveValue('git status --short');
  await typeSql(page, '列出当前目录下大于 100MB 的文件');
  await page.getByRole('button', { name: '自然语言转命令', exact: true }).click();
  await page.getByRole('button', { name: '生成命令', exact: true }).click();
  await expect(input(page)).toHaveValue('find . -type f -size +100M');
  expect(calls.at(-1)?.body).toMatchObject({ instruction: '列出当前目录下大于 100MB 的文件', resource_id: 'ssh-resource', language: 'shell' });
  expect(calls.at(-1)?.headers['x-csrf-token']).toBe('test-csrf');
  expect(calls.at(-1)?.headers.authorization).toBeUndefined();
  expect(blocked).toEqual([]);
  expect(errors).toEqual([]);
});

test('natural language replaces only its line, supports undo and never runs SQL', async ({ page }, info) => {
  const state = await setup(page);
  const source = 'SELECT 1;\n查询 customers 表最新的 10 个 id';
  await typeSql(page, source);
  await page.getByRole('button', { name: '自然语言转SQL', exact: true }).click();
  await expect(page.getByLabel('自然语言需求')).toHaveValue('查询 customers 表最新的 10 个 id');
  await page.screenshot({ path: info.outputPath('natural-language-input.png') });
  await page.getByRole('button', { name: '生成SQL', exact: true }).click();
  await expect.poll(() => savedSql(page)).toBe('SELECT 1;\n' + state.generated.code);
  expect(state.generations[0]).toMatchObject({ instruction: '查询 customers 表最新的 10 个 id', language: 'mysql', schema_context: '' });
  await page.screenshot({ path: info.outputPath('natural-language-result.png') });
  await input(page).press(process.platform === 'darwin' ? 'Meta+z' : 'Control+z');
  await expect.poll(() => savedSql(page)).toBe(source);
  expect(state.blocked).toEqual([]);
  expect(state.errors).toEqual([]);
});

test('keyboard shortcut converts a multiline selection and preserves surrounding SQL', async ({ page }) => {
  const state = await setup(page);
  await typeSql(page, '查询 customers 表\n按 id 倒序取 10 条');
  await input(page).press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
  await input(page).press(process.platform === 'darwin' ? 'Meta+i' : 'Control+i');
  await expect(page.getByLabel('自然语言需求')).toHaveValue('查询 customers 表\n按 id 倒序取 10 条');
  await page.getByLabel('自然语言需求').press('Enter');
  await expect.poll(() => savedSql(page)).toBe(state.generated.code);
  expect(state.errors).toEqual([]);
});

test('generation does not overwrite edits made during a delayed request', async ({ page }) => {
  const state = await setup(page);
  state.delay = 1200;
  await typeSql(page, '查询 customers 表的 id');
  await page.getByRole('button', { name: '自然语言转SQL', exact: true }).click();
  await page.getByRole('button', { name: '生成SQL', exact: true }).click();
  await expect.poll(() => state.generations.length).toBe(1);
  await typeSql(page, 'SELECT 42;');
  await expect(page.getByRole('form', { name: '自然语言生成' }).getByRole('alert')).toContainText('未覆盖你的输入');
  expect(await savedSql(page)).toBe('SELECT 42;');
  expect(state.errors).toEqual([]);
});

test('clarification and provider failure preserve the natural-language draft', async ({ page }) => {
  const state = await setup(page);
  state.generated = { code: '', message: '请指定要查询的表名。' };
  await typeSql(page, '查询用户');
  await page.getByRole('button', { name: '自然语言转SQL', exact: true }).click();
  await page.getByRole('button', { name: '生成SQL', exact: true }).click();
  await expect(page.getByRole('form', { name: '自然语言生成' }).getByRole('alert')).toContainText('请指定要查询的表名');
  expect(await savedSql(page)).toBe('查询用户');
  state.failure = true;
  await page.getByLabel('自然语言需求').fill('查询 customers 表');
  await page.getByRole('button', { name: '生成SQL', exact: true }).click();
  await expect(page.getByRole('form', { name: '自然语言生成' }).getByRole('alert')).toContainText('AI 响应超时');
  expect(await savedSql(page)).toBe('查询用户');
  expect(state.errors).toEqual([]);
});

test('canceling generation keeps the original text', async ({ page }) => {
  const state = await setup(page);
  state.delay = 1200;
  await typeSql(page, '查询 customers 表');
  await page.getByRole('button', { name: '自然语言转SQL', exact: true }).click();
  await page.getByRole('button', { name: '生成SQL', exact: true }).click();
  await expect.poll(() => state.generations.length).toBe(1);
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await page.waitForTimeout(1400);
  expect(await savedSql(page)).toBe('查询 customers 表');
  expect(state.errors).toEqual([]);
});

test('login page removes decorative eyebrows at desktop and narrow sizes', async ({ page }, info) => {
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.goto('/');
  await expect(page.getByRole('button', { name: '登录工作台' })).toBeVisible();
  await expect(page.locator('[class*=eyebrow]')).toHaveCount(0);
  await expect(page.getByText('BUILT FOR YOUR DAILY FLOW')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('login-no-eyebrows.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('button', { name: '登录工作台' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
