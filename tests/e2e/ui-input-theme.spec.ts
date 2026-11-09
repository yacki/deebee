import { expect, test, type Locator, type Page } from '@playwright/test';

async function setup(page: Page) {
  await page.addInitScript(() => sessionStorage.setItem('deebee_token','input-theme-test'));
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    const source = {id:'source-qa',version:1,name:'Managed QA',type:'api_key',validation_mode:'managed',enabled:true};
    return route.fulfill({json:path.endsWith('/identity-sources')?{items:[source]}:{ok:true,items:[],total:0}});
  });
  await page.goto('/admin');
  await expect(page.getByRole('heading',{name:'资源管理',exact:true})).toBeVisible();
}
async function white(control: Locator) {
  await expect(control).toHaveCSS('background-color','rgb(255, 255, 255)');
  await expect(control).toHaveCSS('border-top-width','1px');
  await expect(control).not.toHaveCSS('border-top-color','rgba(0, 0, 0, 0)');
}

test('admin text, password and selection controls stay white while accepting input', async ({page}) => {
  await setup(page);
  await page.getByRole('navigation').getByText('系统账号',{exact:true}).click();
  await page.getByRole('button',{name:'新增',exact:true}).click();
  const name = page.getByLabel('显示名称',{exact:true});
  await white(name.locator('..')); await name.fill('Input theme QA'); await expect(name).toHaveValue('Input theme QA');
  await white(page.getByLabel('登录密码',{exact:true}).locator('..'));
  const type = page.getByRole('combobox',{name:'账号类型',exact:true});
  await white(type); await type.hover(); await white(type); await type.click(); await white(type);
  await page.locator('.arco-select-dropdown:visible .arco-select-option').first().click();
  await page.screenshot({path:'test-results/ui-inputs-admin-form.png',animations:'disabled'});
  await page.getByRole('button',{name:'取消',exact:true}).click();
});

test('admin locked selects retain a disabled appearance and cannot be edited', async ({page}) => {
  await setup(page);
  await page.getByRole('navigation').getByText('系统设置',{exact:true}).click();
  await page.getByRole('button',{name:'身份验证服务',exact:true}).click();
  await page.getByRole('row').filter({hasText:'Managed QA'}).getByRole('button',{name:'编辑',exact:true}).click();
  const type = page.getByRole('combobox',{name:'身份验证服务类型',exact:true});
  await expect(type).toHaveClass(/disabled/);
  await expect(type).not.toHaveCSS('background-color','rgb(255, 255, 255)');
  await expect(type.locator('input')).toBeDisabled();
  await white(page.getByLabel('身份验证服务名称',{exact:true}).locator('..'));
});

test('recording text, select and date filters share the outlined theme and submit entered dates', async ({page}) => {
  await setup(page);
  await page.getByRole('navigation').getByText('会话录像',{exact:true}).click();
  const text = page.getByLabel('服务器 / 用户',{exact:true});
  await white(text.locator('..')); await text.fill('host QA');
  await white(page.getByRole('combobox',{name:'协议',exact:true}));
  const picker = page.locator('.recording-filters .arco-picker').first();
  await white(picker); await picker.hover(); await white(picker);
  await picker.click();await picker.locator('input').fill('2026-10-19 12:00:00');
  await page.getByRole('button',{name:'确定',exact:true}).click();
  await page.keyboard.press('Escape');
  const request = page.waitForRequest(r => r.url().includes('/admin/v1/session-recordings?') && new URL(r.url()).searchParams.get('q') === 'host QA');
  await page.getByRole('button',{name:'查询',exact:true}).click();
  expect(Number(new URL((await request).url()).searchParams.get('after'))).toBe(new Date('2026-10-19T12:00:00').getTime()/1000);
});
