import { test, expect, type Page, type APIRequestContext } from '@playwright/test';

// Opt-in because these tests create configuration in a disposable local backend.
const base = process.env.DEEBEE_GROUP_UI_URL || '';
test.skip(!base, 'Requires an isolated local backend');
test.describe.configure({ mode: 'serial' });
let admin: APIRequestContext;
let token = '';
const resources: {id:string;name:string;version:number;type:string;environment:string}[] = [];
const extraResources: {id:string;name:string;version:number}[] = [];
const hiddenUsername = 'catalog-hidden-reader';
const groupName = '生产运维组';
const memberName = '验收人员 '+Date.now().toString(36);
let memberId = '', groupId = '';
async function api(path:string, method='GET', data?:unknown) {
  const response = await admin.fetch(base+'/api/admin/v1/'+path,{method,data});
  expect(response.ok(),await response.text()).toBeTruthy(); return response.json();
}
async function settled(page:Page) { await expect(page.getByRole('button',{name:'刷新',exact:true})).toBeEnabled(); }
async function nav(page:Page,name:string) { await page.getByRole('navigation').getByText(name,{exact:true}).click();await settled(page); }
async function choose(page:Page,label:string,value:string) {
  await page.getByRole('combobox',{name:label,exact:true}).click();
  await page.locator('.arco-select-dropdown:visible').last().getByText(value,{exact:true}).first().click();await page.keyboard.press('Escape');
}
async function save(page:Page) { await page.getByRole('button',{name:'保存',exact:true}).click();await expect(page.locator('.access-form')).toHaveCount(0);await settled(page); }
async function editResource(page:Page,name:string) {
  await page.locator('.resource-card').filter({hasText:name}).getByRole('button',{name:name+'的更多操作',exact:true}).click();
  await page.getByText('编辑资源',{exact:true}).click();
}
test.beforeAll(async({playwright})=>{
  expect(base).toMatch(/^http:\/\/127\.0\.0\.1:/);
  admin=await playwright.request.newContext();
  const login=await admin.post(base+'/api/auth/login',{data:{username:'admin',password:'deebee'}});token=(await login.json()).token;
  await admin.dispose();admin=await playwright.request.newContext({extraHTTPHeaders:{Authorization:'Bearer '+token}});
  const definitions = [
    ['mysql','订单数据库','prod',3306,'orders'],['postgresql','业务分析库','prod',5432,'analytics'],['rdp','Windows 运维桌面','prod',3389,''],
    ['ssh','应用服务器','dev',22,''],['k8s','测试集群','test',6443,''],['mssql','财务数据库','uat',1433,'finance'],
    ['redis','缓存服务','自定义沙箱',6379,''],['mongodb','文档数据库','',27017,'documents'],['clickhouse','日志分析库','test',8123,'logs'],
  ] as const;
  for (const [index,[type,name,environment,port,database]] of definitions.entries()) resources.push(await api('resources','POST',{
    type,name,environment,port,database,host:'10.20.0.'+(index+10),enabled:['mysql','postgresql','k8s'].includes(type),project_groups:['业务平台'],tags:index%2?['ARM64']:['AMD64'],
  }));
  await api('accounts','POST',{resource_id:resources[0]!.id,name:'数据库只读连接',username:hiddenUsername,password:'isolated_qa_password',enabled:false});
  memberId=(await api('principals','POST',{name:memberName,kind:'human'})).id;
});
test.beforeEach(async({page})=>{
  await page.addInitScript(value=>sessionStorage.setItem('deebee_token',value),token);
  await page.goto(base+'/admin');await expect(page.getByRole('heading',{name:'资源管理',exact:true})).toBeVisible();await settled(page);
});
test.afterAll(async()=>{
  for(const resource of [...resources,...extraResources]){const current=(await api('resources')).items.find((r:{id:string})=>r.id===resource.id);if(current)await api('resources/'+current.id,'DELETE',{expected_version:current.version,confirm_name:current.name});}
  if(groupId){const group=(await api('roles')).items.find((r:{id:string})=>r.id===groupId);if(group)await api('roles/'+group.id,'DELETE',{expected_version:group.version,confirm_name:group.name});}
  await admin?.dispose();
});

test('九类产品图标本地加载，卡片和表格不再展示连接账号',async({page})=>{
  const outside:string[]=[];page.on('request',request=>{if(!request.url().startsWith(base)&&!request.url().startsWith('data:'))outside.push(request.url());});
  await page.setViewportSize({width:1440,height:1100});
  await expect(page.locator('.resource-card')).toHaveCount(9);
  for(const alt of ['MySQL','PostgreSQL','Windows','Linux','Kubernetes','SQL Server','Redis','MongoDB','ClickHouse']) {
    const icon=page.locator('.catalog').getByRole('img',{name:alt,exact:true});await expect(icon).toBeVisible();
    await expect.poll(()=>icon.evaluate(el=>(el as HTMLImageElement).complete&&(el as HTMLImageElement).naturalWidth>0)).toBeTruthy();
  }
  await expect(page.locator('.catalog')).not.toContainText('资源账号');await expect(page.locator('.catalog')).not.toContainText(hiddenUsername);
  await page.screenshot({path:'test-results/resource-catalog-desktop.png',fullPage:true});
  await page.getByRole('radio',{name:'列表',exact:true}).locator('..').click();
  await expect(page.getByRole('columnheader',{name:'资源账号',exact:true})).toHaveCount(0);
  await expect(page.locator('.catalog img')).toHaveCount(9);await expect(page.locator('.catalog')).not.toContainText(hiddenUsername);
  expect(outside).toEqual([]);
});

test('环境分组数量、未分配环境、组合筛选和空结果',async({page})=>{
  const environments=page.getByRole('group',{name:'环境分组'});
  await environments.getByRole('button',{name:/生产环境/}).click();await expect(page.locator('.resource-card')).toHaveCount(3);
  await choose(page,'资源类型筛选','MySQL');await expect(page.locator('.resource-card')).toHaveCount(1);
  await page.getByLabel('搜索资源',{exact:true}).fill('10.20.0.10');await expect(page.locator('.resource-card')).toHaveCount(1);
  await page.getByLabel('搜索资源',{exact:true}).fill('不存在的资源');await expect(page.getByText('没有符合条件的资源',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'重置',exact:true}).click();await expect(page.locator('.resource-card')).toHaveCount(9);
  const unassigned=environments.getByRole('button',{name:/未分配环境/});await unassigned.click();await expect(unassigned).toHaveAttribute('aria-pressed','true');await expect(page.locator('.resource-card')).toHaveCount(1);await expect(page.locator('.resource-card')).toContainText('文档数据库');
  await environments.getByRole('button',{name:/自定义沙箱/}).click();await expect(page.locator('.resource-card')).toContainText('缓存服务');
});

test('自定义环境通过编辑保存，刷新后保留，允许清空回到未分配',async({page})=>{
  await editResource(page,'订单数据库');
  await page.getByRole('combobox',{name:'环境',exact:true}).click();
  await page.getByRole('combobox',{name:'环境',exact:true}).getByRole('textbox').fill('灾备环境');
  await page.locator('.arco-select-dropdown:visible').getByText('灾备环境',{exact:true}).click();await page.getByLabel('资源名称',{exact:true}).click();await save(page);
  expect((await api('resources')).items.find((r:{id:string})=>r.id===resources[0]!.id).environment).toBe('灾备环境');
  await page.reload();await settled(page);await page.getByRole('group',{name:'环境分组'}).getByRole('button',{name:/灾备环境/}).click();await expect(page.locator('.resource-card')).toHaveCount(1);
  await editResource(page,'订单数据库');
  await page.getByRole('combobox',{name:'环境',exact:true}).hover();await page.getByRole('combobox',{name:'环境',exact:true}).locator('.arco-select-view-clear-btn').click();await save(page);
  expect((await api('resources')).items.find((r:{id:string})=>r.id===resources[0]!.id).environment).toBe('');
  await expect(page.getByRole('group',{name:'环境分组'}).getByRole('button',{name:/灾备环境/})).toHaveCount(0);
  await expect(page.locator('.resource-card')).toHaveCount(9);
  const updated=(await api('resources')).items.find((r:{id:string})=>r.id===resources[0]!.id);
  await api('resources/'+updated.id,'PATCH',{expected_version:updated.version,environment:'prod'});
});

test('资源名称和列表详情均能进入详情，连接账号仍可管理',async({page})=>{
  await page.getByRole('button',{name:'订单数据库',exact:true}).click();await settled(page);
  await expect(page.getByRole('heading',{name:'订单数据库 · 资源详情',exact:true})).toBeVisible();
  await expect(page.locator('.resource-context').getByRole('img',{name:'MySQL',exact:true})).toBeVisible();
  await expect(page.getByRole('row').filter({hasText:hiddenUsername})).toBeVisible();
  await expect(page.getByRole('button',{name:'添加资源账号',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'资源管理',exact:true}).click();await settled(page);
  await page.getByRole('radio',{name:'列表',exact:true}).locator('..').click();
  await page.getByRole('row').filter({hasText:'Windows 运维桌面'}).getByRole('button',{name:'详情',exact:true}).click();await settled(page);
  await expect(page.locator('.resource-context').getByRole('img',{name:'Windows',exact:true})).toBeVisible();
});

test('用户组新增、编辑、成员分配、人员归组和授权对象术语一致',async({page})=>{
  test.setTimeout(60000);
  await nav(page,'用户组管理');await page.getByRole('button',{name:'新增',exact:true}).click();
  await page.getByLabel('用户组名称',{exact:true}).fill(groupName);await page.getByLabel('用户组说明',{exact:true}).fill('负责生产环境巡检');await save(page);
  groupId=(await api('roles')).items.find((r:{name:string})=>r.name===groupName).id;
  await page.getByLabel('搜索用户组',{exact:true}).fill(groupName);await page.getByRole('button',{name:groupName,exact:true}).click();
  await page.getByRole('button',{name:'编辑用户组',exact:true}).click();await page.getByLabel('用户组说明',{exact:true}).fill('生产值班成员');await save(page);
  await page.getByRole('button',{name:groupName,exact:true}).click();
  await choose(page,'选择系统账号',memberName);await page.getByRole('button',{name:'添加成员 / 保存有效期',exact:true}).click();
  await expect(page.getByRole('row').filter({hasText:memberName})).toContainText('生效中');
  expect((await api('role-memberships')).items.some((m:{role_id:string;principal_id:string})=>m.role_id===groupId&&m.principal_id===memberId)).toBeTruthy();
  await page.getByRole('button',{name:'添加资源授权',exact:true}).click();await expect(page.getByRole('radio',{name:'用户组',exact:true})).toBeChecked();await expect(page.getByRole('combobox',{name:'用户组',exact:true})).toContainText(groupName);await page.getByRole('button',{name:'取消',exact:true}).click();
  await nav(page,'系统账号');await page.getByRole('row').filter({hasText:memberName}).getByRole('button',{name:'查看关联',exact:true}).click();await settled(page);
  await expect(page.getByRole('heading',{name:'所属用户组',exact:true})).toBeVisible();await expect(page.locator('section[aria-label="用户组成员配置"]')).toContainText(groupName);
  await expect(page.locator('body')).not.toContainText('角色');
});

test('用户组启停、复制、成员移除和删除',async({page})=>{
  test.setTimeout(60000);
  await nav(page,'用户组管理');await page.getByRole('button',{name:groupName,exact:true}).click();
  await page.getByRole('button',{name:'停用用户组',exact:true}).click();await expect(page.getByRole('button',{name:'启用用户组',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'启用用户组',exact:true}).click();await expect(page.getByRole('button',{name:'停用用户组',exact:true})).toBeVisible();
  await page.getByRole('row').filter({hasText:memberName}).getByRole('button',{name:'移除',exact:true}).click();await expect(page.getByRole('row').filter({hasText:memberName})).toContainText('已移除');
  await page.getByRole('button',{name:'复制用户组',exact:true}).click();await page.getByLabel('新用户组名称',{exact:true}).fill('灾备值班组');await page.getByRole('button',{name:'确定',exact:true}).click();await settled(page);
  const copy=(await api('roles')).items.find((r:{name:string})=>r.name==='灾备值班组');expect(copy.enabled).toBe(false);
  await page.getByRole('button',{name:'返回用户组列表',exact:true}).click();await page.getByRole('button',{name:'灾备值班组',exact:true}).click();
  await page.getByRole('button',{name:'删除用户组',exact:true}).click();await page.getByLabel('确认删除的用户组名称',{exact:true}).fill('灾备值班组');await page.getByRole('button',{name:'确定',exact:true}).click();await settled(page);
  await expect(page.getByRole('button',{name:'灾备值班组',exact:true})).toHaveCount(0);
});

test('分页随环境切换归位，窄屏卡片没有水平溢出',async({page})=>{
  const added=[];
  for(let i=0;i<4;i++)added.push(await api('resources','POST',{name:'分页测试 '+i,type:'mysql',host:'127.0.0.1',port:3306,database:'qa',environment:'dev'}));
  extraResources.push(...added);
  await page.getByRole('button',{name:'刷新',exact:true}).click();await settled(page);await expect(page.locator('.resource-card')).toHaveCount(12);
  await page.locator('.arco-pagination-item').filter({hasText:/^2$/}).click();await expect(page.locator('.resource-card')).toHaveCount(1);
  await page.getByRole('group',{name:'环境分组'}).getByRole('button',{name:/生产环境/}).click();await expect(page.locator('.resource-card')).toHaveCount(3);
  await page.setViewportSize({width:780,height:1000});await page.screenshot({path:'test-results/resource-catalog-narrow.png',fullPage:true});
  const sizes=await page.locator('.access-main').evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth}));expect(sizes.scroll).toBeLessThanOrEqual(sizes.width+1);
  await page.getByRole('group',{name:'环境分组'}).getByRole('button',{name:/开发环境/}).click();
  const card=page.locator('.resource-card').filter({hasText:'分页测试 0'});
  await card.getByRole('button',{name:'分页测试 0的更多操作',exact:true}).click();await page.getByText('启用资源',{exact:true}).click();await settled(page);await expect(card).toContainText('已启用');
  await card.getByRole('button',{name:'分页测试 0的更多操作',exact:true}).click();await page.getByText('停用资源',{exact:true}).click();await settled(page);await expect(card).toContainText('未启用');
  await card.getByRole('button',{name:'分页测试 0的更多操作',exact:true}).click();await page.locator('.arco-dropdown-option:visible').getByText('删除资源',{exact:true}).click();await page.getByLabel('确认删除的资源名称',{exact:true}).fill('分页测试 0');await page.getByRole('button',{name:'确认删除',exact:true}).click();await settled(page);await expect(card).toHaveCount(0);
  for(const r of added.slice(1))await api('resources/'+r.id,'DELETE',{expected_version:r.version,confirm_name:r.name});
});

test('人员资源列表显示产品与环境，账号留在选中资源的操作区',async({page})=>{
  await page.route('**/api/v1/me',route=>route.fulfill({json:{principal:{name:memberName},roles:[{name:groupName}],csrf:'qa'}}));
  await page.route('**/api/v1/resources',route=>route.fulfill({json:{resources:[{...resources[0],access_modes:[{access_option_id:'qa',mode:'normal',username:hiddenUsername,role_name:groupName,actions:['db.query']}]}]}}));
  await page.goto(base+'/#access-user');await expect(page.getByText('所属用户组：'+groupName,{exact:true})).toBeVisible();
  await expect(page.locator('.access-resource-cards').getByRole('img',{name:'MySQL',exact:true})).toBeVisible();
  await expect(page.locator('.access-resource-cards')).not.toContainText(hiddenUsername);await expect(page.locator('.access-resource-cards')).toContainText('生产环境');
  await page.locator('.access-resource-cards').getByRole('button').click();await expect(page.getByRole('combobox',{name:'资源账号',exact:true})).toContainText(hiddenUsername);
});
