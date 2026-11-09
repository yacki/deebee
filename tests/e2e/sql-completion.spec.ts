import { expect, test, type Page } from '@playwright/test';
import type { Catalog } from '../../src/types';

const catalog: Catalog = {
  database: 'idmesh',
  tables: [
    { name: 'core_app', object_type: 'BASE TABLE', columns: [
      { name: 'id', data_type: 'bigint', nullable: false },
      { name: 'app_name', data_type: 'varchar(120)', nullable: false },
      { name: 'account_code', data_type: 'varchar(32)', nullable: true },
      { name: 'status', data_type: 'int', nullable: false },
    ] },
    { name: 'core_user', object_type: 'BASE TABLE', columns: [
      { name: 'id', data_type: 'bigint', nullable: false },
      { name: 'user_name', data_type: 'varchar(80)', nullable: false },
      { name: 'app_id', data_type: 'bigint', nullable: false },
    ] },
    { name: 'dev_webhook', object_type: 'BASE TABLE', columns: [
      { name: 'account_status', data_type: 'varchar(30)', nullable: true },
      { name: 'webhook_secret', data_type: 'text', nullable: true },
    ] },
    { name: 'active_apps', object_type: 'VIEW', columns: [{ name: 'app_name', data_type: 'varchar(120)', nullable: false }] },
    { name: 'core_very_long_table_name_for_checking_completion_layout_and_description_clipping', object_type: 'BASE TABLE', columns: [] },
  ],
  routines: [{ name: 'calc_score', object_type: 'FUNCTION', data_type: 'int' }, { name: 'clear_logs', object_type: 'PROCEDURE', data_type: '' }],
};
const sessions = new WeakMap<Page, { errors: string[]; blocked: string[]; profileId: string }>();
const input = (page: Page) => page.locator('.monaco-editor textarea:visible');
const widget = (page: Page) => page.locator('.suggest-widget.visible');
const options = (page: Page) => widget(page).getByRole('option');
const label = (page: Page, name: string) => options(page).filter({ has: page.locator('.label-name', { hasText: new RegExp('^' + name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$') }) });

async function setup(page: Page, dialect: 'mysql' | 'postgresql' | 'mssql' = 'mysql') {
  const state = { errors: [] as string[], blocked: [] as string[], profileId: `completion-${dialect}` };
  sessions.set(page, state);
  page.on('pageerror', error => state.errors.push(error.message));
  const schema = dialect === 'postgresql' ? 'public' : dialect === 'mssql' ? 'dbo' : '';
  const profile = { id: state.profileId, driver: dialect, name: 'SQL 补全测试', host: 'database.invalid', port: 3306, user: 'reader', default_database: 'idmesh', default_schema: schema, options: {} };
  await page.addInitScript(() => sessionStorage.setItem('deebee_token', 'isolated-completion-test'));
  // Every API request is fulfilled here. No backend or database is contacted.
  await page.route('**/api/**', route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let response: unknown = { ok: true, username: 'tester' };
    if (path.endsWith('/connections') && request.method() === 'GET') response = [profile];
    else if (path.endsWith('/databases')) response = [{ name: 'idmesh', charset: 'utf8mb4', collation: '' }];
    else if (path.endsWith('/schemas')) response = [{ name: schema, system: false }];
    else if (path.endsWith('/objects')) response = { tables: catalog.tables.filter(t => t.object_type !== 'VIEW'), views: catalog.tables.filter(t => t.object_type === 'VIEW'), routines: [], triggers: [], events: [] };
    else if (path.endsWith('/catalog')) response = { ...catalog, schema };
    else if (path.endsWith('/sessions')) response = { id: 'isolated-session' };
    else if (path.endsWith('/jobs')) response = [];
    else if (request.method() !== 'GET' && !path.endsWith('/sessions/cleanup') && !/\/sessions\/[^/]+$/.test(path)) {
      state.blocked.push(`${request.method()} ${path}`);
      return route.fulfill({ status: 403, json: { detail: 'Database execution is forbidden in completion tests' } });
    }
    return route.fulfill({ json: response });
  });
  await page.goto(process.env.DEEBEE_WEB_URL ? new URL(process.env.DEEBEE_WEB_URL).pathname : '/');
  await expect(input(page)).toBeVisible();
  await expect(page.locator('.node-label').filter({ hasText: /^core_app$/ }).first()).toBeVisible();
}

async function setSql(page: Page, marked: string, trigger = true) {
  const cursor = marked.includes('|') ? marked.indexOf('|') : marked.length;
  const sql = marked.replace('|', '');
  await input(page).focus();
  await input(page).press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
  await page.keyboard.insertText(sql);
  await input(page).press('Escape');
  for (let index = sql.length; index > cursor; index--) await input(page).press('ArrowLeft');
  if (trigger) await input(page).press('Control+Space');
}
async function labels(page: Page) {
  await expect(options(page).first()).toBeVisible();
  return widget(page).locator('.label-name').allTextContents();
}
async function expectCompletionGeometry(page: Page) {
  const geometry = await widget(page).evaluate(element => {
    const outer = element.getBoundingClientRect();
    const viewport = element.querySelector('.monaco-scrollable-element')!.getBoundingClientRect();
    const rows = [...element.querySelectorAll('.monaco-list-row')];
    return rows.map((row, index) => {
      const rect = row.getBoundingClientRect();
      const visibleTop = Math.max(rect.top, viewport.top, outer.top + 1);
      const visibleBottom = Math.min(rect.bottom, viewport.bottom, outer.bottom - 1);
      const text = (selector: string) => {
        const range = document.createRange();
        range.selectNodeContents(row.querySelector(selector)!);
        return range.getBoundingClientRect();
      };
      const name = text('.label-name');
      const detail = text('.details-label');
      const icon = row.querySelector('.suggest-icon')!.getBoundingClientRect();
      return { index, rowHeight: rect.height, visibleHeight: visibleBottom - visibleTop,
        topGap: visibleTop - outer.top, bottomGap: outer.bottom - visibleBottom,
        nameOffset: (name.top + name.bottom - visibleTop - visibleBottom) / 2,
        detailOffset: (detail.top + detail.bottom - visibleTop - visibleBottom) / 2,
        iconOffset: (icon.top + icon.bottom - visibleTop - visibleBottom) / 2 };
    });
  });
  expect(geometry.length).toBeGreaterThan(0);
  for (const row of geometry) {
    expect(row.visibleHeight, JSON.stringify(row)).toBeCloseTo(row.rowHeight, 1);
    expect(Math.abs(row.nameOffset), JSON.stringify(row)).toBeLessThanOrEqual(1);
    expect(Math.abs(row.detailOffset), JSON.stringify(row)).toBeLessThanOrEqual(1);
    expect(Math.abs(row.iconOffset), JSON.stringify(row)).toBeLessThanOrEqual(1);
  }
  expect(Math.abs(geometry[0].topGap - geometry.at(-1)!.bottomGap), JSON.stringify(geometry)).toBeLessThanOrEqual(0.5);
  return geometry;
}

async function expectSql(page: Page, sql: string) {
  const key = `deebee_workspace:${sessions.get(page)!.profileId}`;
  await expect.poll(() => page.evaluate(k => {
    const saved = JSON.parse(localStorage.getItem(k) || '{}');
    return saved.tabs?.find((tab: {id: string}) => tab.id === saved.activeTabId)?.sql;
  }, key)).toBe(sql);
}

test.afterEach(async ({ page }) => {
  const state = sessions.get(page);
  expect(state?.errors ?? []).toEqual([]);
  expect(state?.blocked ?? []).toEqual([]);
});

test('C01 screenshot reproduction: typing whe automatically selects WHERE and Enter preserves semicolon', async ({ page }, info) => {
  await setup(page);
  await setSql(page, 'SELECT * FROM core_app |;', false);
  await page.keyboard.type('whe', { delay: 100 });
  await expect(label(page, 'WHERE')).toBeVisible();
  expect(await labels(page)).toEqual(['WHERE']);
  await expect(options(page).first()).toHaveClass(/focused/);
  await expect(widget(page).locator('.suggest-status-bar')).not.toBeVisible();
  await info.attach('completion-geometry', { body: JSON.stringify(await expectCompletionGeometry(page), null, 2), contentType: 'application/json' });
  await page.screenshot({ path: info.outputPath('where-fixed.png') });
  await page.locator('.editor-wrap').screenshot({ path: info.outputPath('where-editor.png') });
  await input(page).press('Enter');
  await expectSql(page, 'SELECT * FROM core_app WHERE;');
});

test('C02 a complete table and alias offer clauses, with no unrelated tables, fields or routines', async ({ page }) => {
  await setup(page); await setSql(page, 'SELECT * FROM core_app a ');
  await expect(label(page, 'WHERE')).toBeVisible();
  for (const value of ['core_app', 'dev_webhook', 'app_name', 'webhook_secret', 'calc_score', 'CREATE TABLE']) expect(await labels(page)).not.toContain(value);
});

test('C03 FROM table prefixes use strict matching and concise table/view descriptions', async ({ page }) => {
  await setup(page); await setSql(page, 'SELECT * FROM co');
  await expect(label(page, 'core_app')).toBeVisible();
  expect(await labels(page)).not.toContain('dev_webhook');
  await expect(label(page, 'core_app')).toContainText('表 · 4 列');
  await setSql(page, 'SELECT * FROM act');
  await expect(label(page, 'active_apps')).toContainText('视图 · 1 列');
  await input(page).press('Tab'); await expectSql(page, 'SELECT * FROM active_apps');
});

test('C04 JOIN expects a table, then offers ON after the joined table alias', async ({ page }) => {
  await setup(page); await setSql(page, 'SELECT * FROM core_app a JOIN core_u');
  expect(await labels(page)).toEqual(['core_user']);
  await input(page).press('Tab');
  await page.keyboard.insertText(' u '); await input(page).press('Control+Space');
  await expect(label(page, 'ON')).toBeVisible(); expect(await labels(page)).not.toContain('core_app');
});

test('C05 WHERE fields only come from the referenced table', async ({ page }) => {
  await setup(page); await setSql(page, 'SELECT * FROM core_app WHERE acc');
  await expect(label(page, 'account_code')).toBeVisible();
  expect(await labels(page)).toEqual(['account_code']);
  await expect(label(page, 'account_code')).toContainText('varchar(32) · core_app');
  await setSql(page, 'SELECT * FROM core_app WHERE ');
  expect((await labels(page)).slice(0, 4)).toEqual(['account_code', 'app_name', 'id', 'status']);
  for (const noise of ['AND', 'OR', 'LIMIT', 'WHERE', 'calc_score', 'COUNT']) expect(await labels(page)).not.toContain(noise);
});

test('C06 alias-dot completion contains only its own fields and inserts an unqualified name', async ({ page }) => {
  await setup(page); await setSql(page, 'SELECT * FROM core_app a JOIN core_user u ON a.id=u.app_id WHERE a.');
  await expect(label(page, 'app_name')).toBeVisible();
  expect((await labels(page)).sort()).toEqual(['account_code', 'app_name', 'id', 'status']);
  await label(page, 'app_name').click();
  await expectSql(page, 'SELECT * FROM core_app a JOIN core_user u ON a.id=u.app_id WHERE a.app_name');
});

test('C07 duplicate JOIN fields are qualified once and can be selected by bare field prefix', async ({ page }) => {
  await setup(page); await setSql(page, 'SELECT * FROM core_app a JOIN core_user u ON a.id=u.app_id WHERE i');
  await expect(label(page, 'a.id')).toBeVisible(); await expect(label(page, 'u.id')).toBeVisible();
  expect((await labels(page)).filter(name => name.endsWith('.id')).sort()).toEqual(['a.id', 'u.id']);
  await label(page, 'u.id').click();
  await expectSql(page, 'SELECT * FROM core_app a JOIN core_user u ON a.id=u.app_id WHERE u.id');
});

test('C08 SELECT comma is an expression and reads FROM after the cursor', async ({ page }) => {
  await setup(page); await setSql(page, 'SELECT id, app| FROM core_app;');
  expect(await labels(page)).toEqual(['app_name']);
  await input(page).press('Tab'); await expectSql(page, 'SELECT id, app_name FROM core_app;');
});

test('C09 comma in the FROM list expects another table', async ({ page }) => {
  await setup(page); await setSql(page, 'SELECT * FROM core_app, core_u');
  expect(await labels(page)).toEqual(['core_user']);
});

test('C10 completion in the middle of a token replaces its suffix without damaging surrounding SQL', async ({ page }) => {
  await setup(page); await setSql(page, 'SELECT * FROM core_app whe|re status=1;');
  await expect(label(page, 'WHERE')).toBeVisible(); await input(page).press('Tab');
  await expectSql(page, 'SELECT * FROM core_app WHERE status=1;');
});

test('C11 multiple statements do not leak table fields across semicolons or semicolons in strings', async ({ page }) => {
  await setup(page); await setSql(page, "SELECT ';' FROM core_user; SELECT * FROM core_app WHERE acc");
  expect(await labels(page)).toEqual(['account_code']);
  await setSql(page, 'SELECT * FROM core_user; SELECT * FROM core_app WHERE user_');
  await expect(options(page)).toHaveCount(0);
});

test('C12 nested queries and sibling subqueries resolve their own aliases', async ({ page }) => {
  await setup(page); await setSql(page, 'SELECT (SELECT user_name FROM core_user u WHERE u.id=1) FROM core_app a WHERE EXISTS (SELECT * FROM core_user x WHERE x.user|);');
  expect(await labels(page)).toEqual(['user_name']);
  await setSql(page, 'SELECT * FROM core_app a WHERE EXISTS (SELECT * FROM core_user u WHERE u.id=a.id) AND a.app');
  expect(await labels(page)).toEqual(['app_name']);
});

test('C13 multi-word GROUP BY and ORDER BY continue correctly', async ({ page }) => {
  await setup(page);
  for (const clause of ['GROUP', 'ORDER']) {
    await setSql(page, `SELECT status FROM core_app ${clause} b`);
    expect(await labels(page)).toEqual(['BY']); await input(page).press('Tab');
    await page.keyboard.insertText(' sta'); await input(page).press('Control+Space');
    expect(await labels(page)).toEqual(['status']);
  }
});

test('C14 UPDATE and INSERT offer their own transitions and scoped columns', async ({ page }) => {
  await setup(page); await setSql(page, 'UPDATE core_app se');
  expect(await labels(page)).toEqual(['SET']); await input(page).press('Tab');
  await page.keyboard.insertText(' app'); await input(page).press('Control+Space');
  expect(await labels(page)).toEqual(['app_name']);
  await setSql(page, 'INSERT INTO core_app val'); expect(await labels(page)).toEqual(['VALUES']);
});

test('C15 comments and literal strings suppress even explicit completion', async ({ page }) => {
  await setup(page);
  for (const sql of ["SELECT * FROM core_app WHERE app_name='whe", 'SELECT 1 -- whe', 'SELECT 1 # whe', 'SELECT /* whe', 'SELECT $tag$whe']) {
    await setSql(page, sql); await expect(options(page)).toHaveCount(0);
  }
  await setSql(page, "SELECT * FROM core_app /* FROM core_user; */ whe");
  expect(await labels(page)).toEqual(['WHERE']);
});

test('C16 functions use snippets, filter by prefix, and exclude stored procedures', async ({ page }) => {
  await setup(page); await setSql(page, 'SELECT cou');
  expect(await labels(page)).toEqual(['COUNT']); await input(page).press('Tab');
  await expectSql(page, 'SELECT COUNT()'); await page.keyboard.insertText('*'); await expectSql(page, 'SELECT COUNT(*)');
  await setSql(page, 'SELECT cal'); expect(await labels(page)).toEqual(['calc_score']);
  await setSql(page, 'SELECT cle'); await expect(options(page)).toHaveCount(0);
});

test('C17 PostgreSQL keywords and functions respect the dialect and quoted fields', async ({ page }) => {
  await setup(page, 'postgresql'); await setSql(page, 'SELECT * FROM core_app WHERE app_name il');
  expect(await labels(page)).toEqual(['ILIKE']);
  await setSql(page, 'SELECT jsonb_b'); expect(await labels(page)).toEqual(['JSONB_BUILD_OBJECT']);
  await setSql(page, 'SELECT ifn'); await expect(options(page)).toHaveCount(0);
  await setSql(page, 'SELECT "app|" FROM core_app;');
  expect(await labels(page)).toEqual(['app_name']); await input(page).press('Tab'); await expectSql(page, 'SELECT "app_name" FROM core_app;');
});

test('C18 SQL Server TOP, functions and bracket-quoted fields use the proper dialect', async ({ page }) => {
  await setup(page, 'mssql'); await setSql(page, 'SELECT to'); expect(await labels(page)).toEqual(['TOP']);
  await setSql(page, 'SELECT getd'); expect(await labels(page)).toEqual(['GETDATE']);
  await setSql(page, 'SELECT ifn'); await expect(options(page)).toHaveCount(0);
  await setSql(page, 'SELECT [app|] FROM core_app;');
  expect(await labels(page)).toEqual(['app_name']); await input(page).press('Tab'); await expectSql(page, 'SELECT [app_name] FROM core_app;');
});

test('C19 namespaces, quoted names and unknown qualifiers do not fall back to the whole catalog', async ({ page }) => {
  await setup(page); await setSql(page, 'SELECT * FROM idmesh.core_a'); expect(await labels(page)).toEqual(['core_app']);
  await setSql(page, 'SELECT * FROM `core_a|`;'); expect(await labels(page)).toEqual(['core_app']);
  await input(page).press('Tab'); await expectSql(page, 'SELECT * FROM `core_app`;');
  await setSql(page, 'SELECT * FROM core_app WHERE missing.'); await expect(options(page)).toHaveCount(0);
  await setSql(page, 'SELECT * FROM unknown.core_a'); await expect(options(page)).toHaveCount(0);
  await page.getByRole('button', { name: '新建查询标签', exact: true }).click();
  const tabs = page.locator('.workspace-tabs').getByRole('tab');
  await expect(tabs).toHaveCount(2);
  await expect(tabs.last()).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.view-lines')).toContainText('SELECT 1 AS ready;');
  await setSql(page, 'SELECT * FROM core_user WHERE user_'); expect(await labels(page)).toEqual(['user_name']);
  await page.locator('.workspace-tabs').getByRole('tab').first().click();
  await setSql(page, 'SELECT * FROM core_app WHERE acc'); expect(await labels(page)).toEqual(['account_code']);
});

test('C20 popup fits desktop, narrow and short windows; rows, text and keyboard selection stay readable', async ({ page }, info) => {
  await setup(page);
  for (const viewport of [{ width: 1440, height: 900 }, { width: 1024, height: 768 }, { width: 800, height: 500 }]) {
    await page.setViewportSize(viewport);
    await setSql(page, `SELECT *\nFROM core_app\nWHERE status = 1\nORDER BY status;\nSELECT * FROM ${' '.repeat(36)}co`);
    await expect(label(page, 'core_app')).toBeVisible();
    const box = await widget(page).boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);
    expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height + 1);
    expect(box!.width).toBeGreaterThanOrEqual(300);
    const row = label(page, 'core_app'); expect((await row.boundingBox())!.height).toBeGreaterThanOrEqual(28);
    const text = await row.locator('.label-name').boundingBox();
    const detail = await row.locator('.details-label').boundingBox();
    expect(text!.x + text!.width).toBeLessThanOrEqual(detail!.x + 1);
    for (const description of await widget(page).locator('.details-label').all()) {
      const bounds = await description.boundingBox();
      expect(bounds!.width).toBeGreaterThan(30);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(box!.x + box!.width - 8);
    }
    await expectCompletionGeometry(page);
    await input(page).press('ArrowDown');
    await expect(options(page).nth(1)).toHaveClass(/focused/);
    await page.screenshot({ path: info.outputPath(`popup-${viewport.width}.png`) });
    await input(page).press('Escape'); await expect(widget(page)).toHaveCount(0);
  }
});


test.describe('high density completion layout', () => {
  test.use({ deviceScaleFactor: 2 });
  test('C21 single and multiple candidates have symmetric whitespace and centered text at DPR 2', async ({ page }, info) => {
    await setup(page);
    for (const width of [1440, 800]) {
      await page.setViewportSize({ width, height: 700 });
      for (const [name, sql] of [['single', 'SELECT * FROM core_app whe'], ['multiple', 'SELECT * FROM co']]) {
        await setSql(page, sql);
        await expect(options(page).first()).toBeVisible();
        await expectCompletionGeometry(page);
        const editor = (await page.locator('.editor-wrap').boundingBox())!;
        await page.screenshot({ path: info.outputPath(`${name}-${width}-dpr2.png`), clip: { ...editor, height: name === 'single' ? 90 : 160 } });
      }
    }
  });
});
