import { test, expect, type Page, type APIRequestContext } from '@playwright/test';

const base = process.env.DEEBEE_ROLE_UI_URL || '';
test.skip(!base, 'Requires the isolated role lab; never run against production');
test.describe.configure({ mode: 'serial' });
test.use({ timezoneId: 'Australia/Perth' });
// These credentials belong only to the disposable loopback acceptance server.
const password = 'role_ui_test_password';
const suffix = Date.now().toString(36);
const roleName = `用户组验收 ${suffix}`, personName = `用户组成员 ${suffix}`, secondName = `第二成员 ${suffix}`;
let admin: APIRequestContext;
let roleId = '', personId = '', memberId = '';
let ssh: {id:string;name:string}, mysql: {id:string;name:string};
let accounts: {id:string;resource_id:string;username:string;tier:string;enabled:boolean;name:string}[];
let userPage: Page;
async function api(path:string, method='GET', data?:unknown) {
  const r = await admin.fetch(base + '/api/admin/v1/' + path, { method, data });
  expect(r.ok(), await r.text()).toBeTruthy(); return r.json();
}
async function choose(page:Page, label:string, value:string) {
  await page.getByRole('combobox', {name:label,exact:true}).click();
  await page.locator('.arco-select-dropdown:visible').last().getByText(value,{exact:true}).first().click();
  await page.keyboard.press('Escape');
}
async function settled(page:Page) { await expect(page.getByRole('button',{name:'刷新',exact:true})).toBeEnabled(); }
async function nav(page:Page, name:string) { await page.getByRole('navigation').getByText(name,{exact:true}).click(); await settled(page); }
async function save(page:Page) { await page.getByRole('button',{name:'保存',exact:true}).click(); await expect(page.locator('.access-form')).toHaveCount(0); await settled(page); }
async function openRole(page:Page) { await nav(page,'用户组管理'); await page.getByLabel('搜索用户组',{exact:true}).fill(roleName); await page.getByRole('button',{name:roleName,exact:true}).click(); await expect(page.getByRole('heading',{name:roleName,exact:true})).toBeVisible(); }

test.beforeAll(async ({playwright})=>{
  expect(base).toMatch(/^http:\/\/127\.0\.0\.1:/);
  admin = await playwright.request.newContext();
  const login = await admin.post(base+'/api/auth/login',{data:{username:'admin',password:'deebee'}});
  const token = (await login.json()).token;
  await admin.dispose(); admin = await playwright.request.newContext({extraHTTPHeaders:{Authorization:'Bearer '+token}});
  const resources = (await api('resources')).items;
  accounts = (await api('accounts')).items;
  ssh = resources.find((r:{type:string;enabled:boolean;id:string})=>r.type==='ssh'&&r.enabled&&accounts.some(a=>a.resource_id===r.id&&a.tier==='normal'&&a.enabled));
  mysql = resources.find((r:{type:string;enabled:boolean;id:string})=>r.type==='mysql'&&r.enabled&&accounts.some(a=>a.resource_id===r.id&&a.tier==='normal'&&a.enabled));
  expect(ssh).toBeTruthy(); expect(mysql).toBeTruthy();
  personId=(await api('principals','POST',{name:personName,kind:'human',username:'role_ui_'+suffix,password})).id;
  await api('principals','POST',{name:secondName,kind:'service'});
});
test.beforeEach(async({page})=>{
  const login=await page.request.post(base+'/api/auth/login',{data:{username:'admin',password:'deebee'}});
  const token=(await login.json()).token;
  await page.addInitScript(token=>sessionStorage.setItem('deebee_token',token),token);
  await page.goto(base+'/admin'); await expect(page.getByRole('heading',{name:'资源管理',exact:true})).toBeVisible(); await settled(page);
});
test.afterAll(async()=>{await userPage?.context().close();await admin?.dispose();});

test('用户组新增、编辑、启停、搜索和详情',async({page})=>{
  await nav(page,'用户组管理'); await page.getByRole('button',{name:'新增',exact:true}).click();
  await page.getByLabel('用户组名称',{exact:true}).fill(roleName); await page.getByLabel('用户组说明',{exact:true}).fill('生产巡检与数据库查询'); await save(page);
  roleId=(await api('roles')).items.find((r:{name:string})=>r.name===roleName).id;
  await page.getByLabel('搜索用户组',{exact:true}).fill(roleName); await expect(page.getByRole('row').filter({hasText:roleName})).toHaveCount(1);
  await page.getByRole('button',{name:roleName,exact:true}).click(); await page.getByRole('button',{name:'停用用户组',exact:true}).click(); await settled(page);
  await expect(page.getByRole('heading',{name:roleName,exact:true}).locator('..')).toContainText('已停用');
  await page.getByRole('button',{name:'启用用户组',exact:true}).click(); await settled(page);
  await page.getByRole('button',{name:'编辑用户组',exact:true}).click(); await page.getByLabel('用户组说明',{exact:true}).fill('已验证的资源访问用户组'); await save(page);
  await expect(page.getByRole('row').filter({hasText:roleName})).toContainText('已验证的资源访问用户组');
});

test('用户组批量添加人员和 Agent、成员有效期与编辑',async({page})=>{
  await openRole(page); await choose(page,'选择系统账号',personName); await choose(page,'选择系统账号',secondName);
  await page.getByLabel('成员开始时间',{exact:true}).fill('2020-01-01T00:00'); await page.getByLabel('成员到期时间',{exact:true}).fill('2030-12-31T23:59');
  await page.getByRole('button',{name:'添加成员 / 保存有效期',exact:true}).click(); await expect(page.getByRole('row').filter({hasText:personName})).toContainText('生效中');
  await expect(page.getByRole('row').filter({hasText:secondName})).toContainText('生效中');
  const members=(await api('role-memberships')).items; memberId=members.find((m:{role_id:string;principal_id:string})=>m.role_id===roleId&&m.principal_id===personId).id;
  await page.getByRole('row').filter({hasText:personName}).getByRole('button',{name:'编辑有效期',exact:true}).click(); await expect(page.getByLabel('成员到期时间',{exact:true})).toHaveValue('2030-12-31T23:59');
  await page.getByRole('button',{name:'添加成员 / 保存有效期',exact:true}).click();
});

test('用户组按资源绑定账号、批量授权和操作限制',async({page})=>{
  await openRole(page); await page.getByRole('button',{name:'添加资源授权',exact:true}).click();
  await expect(page.getByRole('combobox',{name:'用户组',exact:true})).toContainText(roleName);
  await choose(page,'目标资源',ssh.name);
  const account=accounts.find(a=>a.resource_id===ssh.id&&a.enabled&&a.tier==='normal')!;
  await page.getByRole('checkbox',{name:new RegExp(account.username+' · '+account.name)}).locator('..').click();
  for(const name of ['执行服务器命令','打开交互终端'])await page.getByRole('checkbox',{name,exact:false}).locator('..').click();
  await choose(page,'同时授权其他资源',mysql.name);
  const group=page.getByRole('group',{name:mysql.name,exact:true});
  const dbAccount=accounts.find(a=>a.resource_id===mysql.id&&a.enabled&&a.tier==='normal')!;
  await group.getByRole('checkbox',{name:dbAccount.username+' · 普通账号',exact:true}).locator('..').click();
  await group.getByRole('checkbox',{name:'查询数据库',exact:true}).locator('..').click();
  await page.getByLabel('授权开始时间',{exact:true}).fill('2020-01-01T00:00'); await page.getByLabel('授权到期时间',{exact:true}).fill('2030-12-31T23:59');
  await page.getByText('执行限制',{exact:true}).click(); await page.getByLabel('执行超时（秒）',{exact:true}).fill('30');
  await save(page);
  const grants=(await api('access-grants')).items.filter((g:{role_id:string})=>g.role_id===roleId);
  expect(grants).toHaveLength(2); expect(grants.every((g:{limits:{timeout_seconds:number}})=>g.limits.timeout_seconds===30)).toBeTruthy();
  await openRole(page); await page.getByRole('row').filter({hasText:ssh.name}).getByRole('button',{name:'修改授权',exact:true}).click();
  await expect(page.getByLabel('授权到期时间',{exact:true})).toHaveValue('2030-12-31T23:59');
  await page.getByText('执行限制',{exact:true}).click(); await page.getByLabel('执行超时（秒）',{exact:true}).fill('20'); await save(page);
  await openRole(page); await expect(page.locator('.access-panel')).toContainText(ssh.name); await expect(page.locator('.access-panel')).toContainText(mysql.name);
  await page.getByRole('button',{name:'查看用户组活动',exact:true}).click(); await expect(page.getByText(/grants.create/).first()).toBeVisible();
  await page.locator('.access-main').evaluate(el=>el.scrollTop=0);
  await page.screenshot({path:'test-results/roles-detail.png',fullPage:true});
});

test('人员侧继承展示、最终权限预览与资源账号反查',async({page})=>{
  const temporary = [await api('roles','POST',{name:'临时用户组一 '+suffix}),await api('roles','POST',{name:'临时用户组二 '+suffix})];
  await nav(page,'系统账号'); await page.getByLabel('搜索系统账号',{exact:true}).fill(personName); await page.getByRole('row').filter({hasText:personName}).getByRole('button',{name:'查看关联',exact:true}).click();
  await expect(page.locator('.access-panel')).toContainText(roleName); await expect(page.locator('.access-panel')).toContainText('通过用户组继承的资源账号');
  for(const role of temporary)await choose(page,'选择用户组',role.name);
  await page.getByLabel('成员开始时间',{exact:true}).fill('2030-01-01T00:00'); await page.getByRole('button',{name:'分配用户组 / 保存有效期',exact:true}).click();
  for(const role of temporary)await expect(page.getByRole('row').filter({hasText:role.name})).toContainText('未生效');
  for(const role of temporary)await choose(page,'选择用户组',role.name);
  await page.getByRole('button',{name:'移除所选',exact:true}).click();
  for(const role of temporary)await expect(page.getByRole('row').filter({hasText:role.name})).toContainText('已移除');
  await page.getByRole('button',{name:'查看最终可用权限',exact:true}).click(); await expect(page.locator('.report-content')).toContainText(ssh.name); await expect(page.locator('.report-content')).toContainText(mysql.name);
  await page.locator('.arco-modal:visible .arco-modal-close-btn').click();
  await nav(page,'资源管理'); await page.locator('.resource-card').filter({hasText:ssh.name}).getByRole('button',{name:'查看详情',exact:true}).click(); await settled(page);
  const account=accounts.find(a=>a.resource_id===ssh.id&&a.enabled&&a.tier==='normal')!;
  const row=page.getByRole('row').filter({hasText:account.name}); await expect(row).toContainText(roleName); await expect(row).toContainText(personName); await expect(row).toContainText(secondName);
});

test('人员真实 SQL 查询、交互终端、多个用户组账号选择及撤权断开',async({page,browser})=>{
  test.setTimeout(120000);
  const context=await browser.newContext({timezoneId:'Australia/Perth'}); userPage=await context.newPage();
  const issues:string[]=[];userPage.on('pageerror',e=>issues.push(e.message));
  await userPage.goto(base+'/#access-user'); await userPage.getByLabel('用户名',{exact:true}).fill('role_ui_'+suffix); await userPage.getByLabel('密码',{exact:true}).fill(password); await userPage.getByRole('button',{name:'登录',exact:true}).click();
  await expect(userPage.getByRole('heading',{name:personName+' 的可用资源',exact:true})).toBeVisible(); await expect(userPage.getByText('所属用户组：'+roleName,{exact:true})).toBeVisible();
  await userPage.locator('.access-resource-cards button').filter({hasText:mysql.name}).click(); await userPage.locator('.access-user-main textarea').fill('SELECT name FROM items WHERE id=1'); await userPage.getByRole('button',{name:'提交执行',exact:true}).click();
  await expect.poll(async()=>{await userPage.getByRole('button',{name:'刷新结果',exact:true}).click();return await userPage.locator('.access-user-main').innerText();}).toContain('Alpha');
  await userPage.locator('.access-resource-cards button').filter({hasText:ssh.name}).click(); await choose(userPage,'操作','打开交互终端'); await userPage.getByRole('button',{name:'打开会话',exact:true}).click();
  await expect(userPage.locator('.ssh-session .remote-dot.connected')).toBeVisible({timeout:15000});
  await userPage.locator('.ssh-session .xterm-helper-textarea').focus(); await userPage.keyboard.type('printf role_ui_shell'); await userPage.keyboard.press('Enter'); await expect(userPage.locator('.ssh-session .xterm-rows')).toContainText('role_ui_shell');
  await userPage.screenshot({path:'test-results/person-shell.png',fullPage:true});
  await openRole(page); await page.getByRole('row').filter({hasText:personName}).getByRole('button',{name:'移除',exact:true}).click();
  await expect(userPage.locator('.ssh-session .remote-dot.error, .ssh-session .remote-dot.closed')).toBeVisible({timeout:5000});
  await userPage.getByRole('button',{name:'刷新可用资源',exact:true}).click(); await expect(userPage.getByText('暂无可用资源账号，请联系管理员配置资源授权。',{exact:true})).toBeVisible();
  // Reassign this member plus a distinct high-account role: the UI must retain both choices.
  const member=(await api('role-memberships')).items.find((m:{id:string})=>m.id===memberId);
  await api('role-memberships/'+member.id,'PATCH',{expected_version:member.version,enabled:true});
  const alternative=await api('roles','POST',{name:'备选用户组 '+suffix});
  await api('role-memberships','POST',{role_id:alternative.id,principal_id:personId});
  const high=accounts.find(a=>a.resource_id===ssh.id&&a.enabled&&a.tier==='privileged')!;
  await api('access-grants','POST',{role_id:alternative.id,resource_id:ssh.id,privileged_account_id:high.id,allow_privileged:true,actions:['resources:read','ssh:inspect','privilege:use']});
  await userPage.getByRole('button',{name:'刷新可用资源',exact:true}).click(); await userPage.locator('.access-resource-cards button').filter({hasText:ssh.name}).click();
  await choose(userPage,'资源账号',high.username+' · 高权限账号 · '+alternative.name); await expect(userPage.locator('.access-warning')).toContainText('高权限资源账号');
  await userPage.getByRole('combobox',{name:'操作',exact:true}).click(); await expect(userPage.locator('.arco-select-dropdown:visible').last()).not.toContainText('执行服务器命令'); await userPage.keyboard.press('Escape');
  expect(issues).toEqual([]);
});

test('复制用户组不复制成员或启用授权、删除保留审计与配置导出',async({page})=>{
  await openRole(page); await page.getByRole('button',{name:'复制用户组',exact:true}).click(); await page.getByLabel('新用户组名称',{exact:true}).fill(roleName+' 副本'); await page.locator('.arco-modal:visible .arco-modal-footer button').filter({hasText:'确定'}).click(); await expect(page.locator('.arco-modal:visible')).toHaveCount(0);
  const copied=(await api('roles')).items.find((r:{name:string})=>r.name===roleName+' 副本'); expect(copied.enabled).toBe(false);
  expect((await api('role-memberships')).items.filter((m:{role_id:string})=>m.role_id===copied.id)).toHaveLength(0);
  const grants=(await api('access-grants')).items.filter((g:{role_id:string})=>g.role_id===copied.id); expect(grants).toHaveLength(2); expect(grants.every((g:{enabled:boolean})=>!g.enabled)).toBeTruthy();
  const config=await api('access-config/export'); expect(config.version).toBe(2); expect(config.roles.some((r:{id:string})=>r.id===roleId)).toBeTruthy();
  await page.getByRole('button',{name:'返回用户组列表',exact:true}).click(); await page.getByRole('button',{name:copied.name,exact:true}).click(); await page.getByRole('button',{name:'删除用户组',exact:true}).click(); await page.getByLabel('确认删除的用户组名称',{exact:true}).fill(copied.name); await page.locator('.arco-modal:visible .arco-modal-footer button').filter({hasText:'确定'}).click(); await expect(page.locator('.arco-modal:visible')).toHaveCount(0);
  expect((await api('roles')).items.some((r:{id:string})=>r.id===copied.id)).toBe(false); expect((await api('audit-events?operation=roles.delete')).items.some((a:{object_id:string})=>a.object_id===copied.id)).toBeTruthy();
});

test('RDP 用户组账号核验、真实桌面画面、录像与撤权断开',async({browser})=>{
  test.skip(!process.env.DEEBEE_ROLE_RDP_HOST,'Requires the disposable xrdp and guacd containers with a trusted test certificate');
  test.setTimeout(120000);
  const resource=await api('resources','POST',{name:'RDP 用户组验收 '+suffix,type:'rdp',host:process.env.DEEBEE_ROLE_RDP_HOST,port:3389,rdp_security:'tls',enabled:true});
  let account=await api('accounts','POST',{name:'RDP 受限账号',resource_id:resource.id,username:'deebee',password:'deebee-secret',tier:'privileged'});
  account=await api('accounts/'+account.id+'/test','POST');expect(account.test_result.connected).toBe(true);expect(account.test_result.normal_safe).toBe(false);
  account=await api('accounts/'+account.id,'PATCH',{expected_version:account.version,permission_confirmed:true,permission_note:'隔离 xrdp 测试账号，交互操作按高权限类别发布',enabled:true});
  const role=await api('roles','POST',{name:'RDP 用户组 '+suffix});
  const human=await api('principals','POST',{name:'RDP 人员 '+suffix,username:'role_rdp_'+suffix,password});
  await api('role-memberships','POST',{role_id:role.id,principal_id:human.id});
  await api('access-grants','POST',{role_id:role.id,resource_id:resource.id,privileged_account_id:account.id,allow_privileged:true,actions:['resources:read','rdp:connect','privilege:use']});
  const context=await browser.newContext({timezoneId:'Australia/Perth'});const person=await context.newPage();
  try{
    await person.goto(base+'/#access-user');await person.getByLabel('用户名',{exact:true}).fill(human.username);await person.getByLabel('密码',{exact:true}).fill(password);await person.getByRole('button',{name:'登录',exact:true}).click();
    await expect(person.getByRole('heading',{name:human.name+' 的可用资源',exact:true})).toBeVisible();await person.locator('.access-resource-cards button').filter({hasText:resource.name}).click();
    await expect(person.getByRole('combobox',{name:'操作',exact:true})).toContainText('连接远程桌面');await person.getByRole('button',{name:'打开会话',exact:true}).click();
    await expect(person.locator('.rdp-session .remote-dot.connected')).toBeVisible({timeout:45000});
    await expect(person.locator('.rdp-session canvas').first()).toBeVisible();
    await person.screenshot({path:'test-results/role-rdp-desktop.png',fullPage:true});
    await api('roles/'+role.id,'PATCH',{expected_version:role.version,enabled:false});
    await expect(person.locator('.rdp-session .remote-dot.error, .rdp-session .remote-dot.closed')).toBeVisible({timeout:5000});
    await expect.poll(async()=>{const recordings=await api('session-recordings?protocol=rdp&q='+encodeURIComponent(human.id));return recordings.items.some((r:{actor:string;status:string})=>r.actor===human.id&&r.status!=='recording');}).toBe(true);
    const audit=(await api('audit-events?operation=rdp.session.close')).items.find((a:{actor:string})=>a.actor===human.id);expect(audit.detail.role_id).toBe(role.id);expect(audit.account_username).toBe('deebee');
  }finally{await context.close();}
});
