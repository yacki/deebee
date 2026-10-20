import { test, expect, type Page, type Locator } from '@playwright/test';
import { readFileSync } from 'node:fs';

const qaUrl=process.env.DEEBEE_ADMIN_QA_URL||'';
if(qaUrl&&!['127.0.0.1','localhost','[::1]'].includes(new URL(qaUrl).hostname))throw new Error('Admin QA must use a private loopback instance, never the production URL.');
const credentials=qaUrl?JSON.parse(readFileSync(process.env.DEEBEE_ADMIN_QA_CREDENTIALS||'/tmp/deebee-admin-qa.credentials.json','utf8')):{username:'',password:''};
test.skip(!qaUrl,'Set DEEBEE_ADMIN_QA_URL for the isolated instance with real backend and resource configuration.');
test.describe.configure({mode:'serial',timeout:90000});
let issues:string[]=[];
const rootPath='/deebee';
async function nav(page:Page,name:string){await settled(page);if(await page.locator('.arco-modal:visible').count()){await page.keyboard.press('Escape');await expect(page.locator('.arco-modal:visible')).toHaveCount(0);}await page.getByRole('navigation',{name:'管理后台导航'}).getByText(name,{exact:true}).click();await expect(page.getByRole('heading',{name,exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'刷新',exact:true})).toBeEnabled();}
function field(page:Page,name:string):Locator{return page.locator('.access-fields > label,.access-fields > .access-field').filter({has:page.getByText(name,{exact:true})});}
async function pickField(page:Page,name:string,value:string){await field(page,name).locator('.arco-select').click();await page.locator('.arco-select-dropdown:visible').getByText(value,{exact:true}).click();}
async function save(page:Page){await page.getByRole('button',{name:'保存',exact:true}).click();await expect(page.locator('.access-alert.arco-alert-error')).toHaveCount(0);await expect(page.locator('.access-form')).toHaveCount(0);}
async function api(page:Page,path:string,method='GET',data?:unknown){
  const token=await page.evaluate(()=>sessionStorage.getItem('deebee_token')||'');
  const response=await page.request.fetch(new URL(rootPath+'/api'+path,qaUrl).toString(),{method,data,headers:{Authorization:'Bearer '+token}});
  const body=await response.json();expect(response.ok(),body.error?.message).toBeTruthy();return body;
}
test.beforeEach(async({page})=>{
  issues=[];page.on('pageerror',e=>issues.push(e.message));
  await page.goto(qaUrl);await page.getByLabel('用户名',{exact:true}).fill(credentials.username);await page.getByLabel('密码',{exact:true}).fill(credentials.password);await page.getByRole('button',{name:'登录',exact:true}).click();await expect(page.getByRole('heading',{name:'资源管理',exact:true})).toBeVisible();await settled(page);
});
test.afterEach(()=>expect(issues).toEqual([]));

test('真实后台的账号类型下拉能展开并选择，表单不会消失',async({page})=>{
  await nav(page,'系统账号');await page.getByRole('button',{name:'新增',exact:true}).click();
  await pickField(page,'账号类型','程序 / Agent');
  await expect(page.getByRole('heading',{name:'新增系统账号'})).toBeVisible();
  await expect(page.getByLabel('登录用户名',{exact:true})).toHaveCount(0);
  await pickField(page,'账号类型','人员');
  await expect(page.getByLabel('登录用户名',{exact:true})).toBeVisible();
});

const suffix=Date.now().toString(36);
const principalName=`后台巡检 ${suffix}`;
let principalId='';
let key='';
let grantId='';
let sshId='';
let accountId='';
function row(page:Page,text:string){return page.getByRole('row').filter({hasText:text});}
async function settled(page:Page){await expect(page.getByRole('button',{name:'刷新',exact:true})).toBeEnabled();}
async function choose(page:Page,label:string,value:string){await page.getByRole('combobox',{name:label,exact:true}).click();await page.locator('.arco-select-dropdown:visible').getByText(value,{exact:true}).click();}

test('系统账号新增、编辑、启停与关联详情',async({page})=>{
  await nav(page,'系统账号');await page.getByRole('button',{name:'新增',exact:true}).click();
  await page.getByLabel('显示名称',{exact:true}).fill(principalName);await choose(page,'账号类型','程序 / Agent');
  await page.getByRole('checkbox',{name:'启用系统账号',exact:true}).locator('..').click();await expect(page.getByRole('checkbox',{name:'启用系统账号',exact:true})).not.toBeChecked();
  await save(page);await settled(page);await expect(row(page,principalName)).toContainText('已停用');
  await row(page,principalName).getByRole('button',{name:'编辑',exact:true}).click();await page.getByRole('checkbox',{name:'启用系统账号',exact:true}).locator('..').click();await save(page);await settled(page);
  await row(page,principalName).getByRole('button',{name:'停用',exact:true}).click();await settled(page);await expect(row(page,principalName)).toContainText('已停用');
  await row(page,principalName).getByRole('button',{name:'启用',exact:true}).click();await settled(page);
  principalId=(await api(page,'/admin/v1/principals')).items.find((p:{name:string})=>p.name===principalName).id;
  await row(page,principalName).getByRole('button',{name:'查看关联',exact:true}).click();await settled(page);await expect(page.getByRole('heading',{name:principalName,exact:true})).toBeVisible();
  await page.getByRole('button',{name:'管理外部身份',exact:true}).click();await settled(page);await expect(page.getByRole('heading',{name:'外部身份',exact:true})).toBeVisible();
});

test('身份验证服务各类型切换、保存、编辑限制、检查和外部身份绑定',async({page})=>{
  await nav(page,'系统设置');await page.getByRole('button',{name:'身份验证服务',exact:true}).click();await settled(page);await page.getByRole('button',{name:'新增',exact:true}).click();
  await choose(page,'身份验证服务类型','企业登录（OIDC）');await expect(page.getByLabel('OIDC Issuer',{exact:true})).toBeVisible();
  await choose(page,'验证方式','向登录服务核验令牌');await expect(page.getByLabel('Token Introspection 地址',{exact:true})).toBeVisible();
  await page.getByRole('checkbox',{name:'启用 OIDC 浏览器登录',exact:true}).locator('..').click();await expect(page.getByLabel('浏览器登录 Client ID',{exact:true})).toBeVisible();
  await choose(page,'身份验证服务类型','API Key');await choose(page,'验证方式','由外部服务验证');await expect(page.getByLabel('外部 API-Key 验证地址',{exact:true})).toBeVisible();
  await choose(page,'验证方式','由 DeeBee 验证');await page.getByLabel('身份验证服务名称',{exact:true}).fill(`巡检签发服务 ${suffix}`);await page.getByRole('checkbox',{name:'启用身份验证服务',exact:true}).locator('..').click();
  await save(page);await settled(page);
  await row(page,`巡检签发服务 ${suffix}`).getByRole('button',{name:'编辑',exact:true}).click();await expect(page.getByRole('combobox',{name:'身份验证服务类型',exact:true})).toHaveClass(/disabled/);await expect(page.getByRole('combobox',{name:'验证方式',exact:true})).toHaveClass(/disabled/);await save(page);await settled(page);
  await row(page,`巡检签发服务 ${suffix}`).getByRole('button',{name:'检查验证服务',exact:true}).click();await page.getByRole('button',{name:'检查',exact:true}).click();await settled(page);await expect(page.getByText('身份验证服务测试结果',{exact:true})).toBeVisible();
  await nav(page,'外部身份');await page.getByRole('button',{name:'绑定外部身份',exact:true}).click();await choose(page,'身份验证服务',`巡检签发服务 ${suffix}`);await choose(page,'系统账号',principalName);await page.getByLabel('外部身份标识',{exact:true}).fill(`qa-admin-${suffix}`);await save(page);await settled(page);
  const binding=row(page,`qa-admin-${suffix}`);await expect(binding).toContainText(principalName);await binding.getByRole('button',{name:'停用',exact:true}).click();await settled(page);await binding.getByRole('button',{name:'修改绑定',exact:true}).click();await page.getByRole('checkbox',{name:'启用外部身份',exact:true}).locator('..').click();await save(page);await settled(page);
});

test('真实资源分类、五种筛选、列表切换和账号连接检查',async({page})=>{
  test.setTimeout(120000);
  const resources=(await api(page,'/admin/v1/resources')).items;
  const ssh=resources.find((r:{name:string})=>r.name==='Linux 8.159.155.8');sshId=ssh.id;
  accountId=(await api(page,'/admin/v1/accounts')).items.find((a:{resource_id:string;tier:string})=>a.resource_id===sshId&&a.tier==='normal').id;
  await page.locator('.resource-card').filter({hasText:'Linux 8.159.155.8'}).getByRole('button',{name:'Linux 8.159.155.8的更多操作',exact:true}).click();await page.getByText('编辑资源',{exact:true}).click();
  await choose(page,'环境','uat');
  if(!(await page.getByRole('combobox',{name:'项目组',exact:true}).innerText()).includes('后台巡检')){await page.getByRole('combobox',{name:'项目组',exact:true}).getByRole('textbox').fill('后台巡检');await page.locator('.arco-select-dropdown:visible').getByText('后台巡检',{exact:true}).click();}
  if(!(await page.getByRole('combobox',{name:'标签',exact:true}).innerText()).includes('AMD64'))await choose(page,'标签','AMD64');await page.getByLabel('资源名称',{exact:true}).click();await save(page);await settled(page);
  await page.getByRole('group',{name:'环境分组'}).getByRole('button',{name:/验收环境/}).click();
  for(const [label,value] of [['项目组筛选','后台巡检'],['标签筛选','AMD64'],['资源类型筛选','Linux / SSH'],['资源状态筛选','已启用']]){await choose(page,label,value);await expect(page.locator('.resource-card')).toHaveCount(1);}
  await page.getByLabel('搜索资源',{exact:true}).fill('8.159.155.8');await expect(page.locator('.resource-card')).toHaveCount(1);
  await page.getByRole('radio',{name:'列表',exact:true}).locator('..').click();await expect(page.getByRole('row').filter({hasText:'Linux 8.159.155.8'})).toHaveCount(1);
  await page.getByRole('radio',{name:'卡片',exact:true}).locator('..').click();await page.getByRole('button',{name:'重置',exact:true}).click();await expect(page.locator('.resource-card').filter({hasText:'IDMesh MySQL / idmesh'})).toBeVisible();
  await page.locator('.resource-card').filter({hasText:'Linux 8.159.155.8'}).getByRole('button',{name:'查看详情',exact:true}).click();await settled(page);
  await row(page,'deebee-mcp').getByRole('button',{name:'编辑',exact:true}).click();await choose(page,'登录方式','SSH 私钥');await expect(page.getByLabel('SSH 私钥',{exact:true})).toBeVisible();await choose(page,'登录方式','密码');await expect(page.getByLabel('密码 / Kubernetes Bearer Token',{exact:true})).toBeVisible();await page.getByRole('button',{name:'取消',exact:true}).click();
  await row(page,'deebee-mcp').getByRole('button',{name:'检查连接与权限',exact:true}).click();await settled(page);await expect(page.getByText('资源账号检查结果',{exact:true})).toBeVisible();
  const account=(await api(page,'/admin/v1/accounts')).items.find((a:{id:string})=>a.id===accountId);expect(account.test_result.connected).toBeTruthy();
});

test('资源授权的级别、操作复选框、日期、编辑和权限预览',async({page})=>{
  await nav(page,'资源授权');await page.getByRole('button',{name:'新增',exact:true}).click();await choose(page,'系统账号',principalName);await choose(page,'目标资源','Linux 8.159.155.8');
  await page.getByRole('checkbox',{name:/deebee-mcp ·/}).locator('..').click();await page.getByRole('checkbox',{name:'只读检查服务器',exact:true}).locator('..').click();
  await page.getByLabel('授权到期时间',{exact:true}).fill('2026-12-31T23:30');await page.getByText('执行限制',{exact:true}).click();await page.getByLabel('执行超时（秒）',{exact:true}).fill('30');await save(page);await settled(page);
  const grant=(await api(page,'/admin/v1/access-grants')).items.find((g:{principal_id:string;resource_id:string})=>g.principal_id===principalId&&g.resource_id===sshId);grantId=grant.id;expect(grant.actions).toContain('ssh:inspect');expect(grant.limits.timeout_seconds).toBe(30);expect(grant.expires_at).toBeGreaterThan(Date.now()/1000);
  await row(page,principalName).getByRole('button',{name:'修改授权',exact:true}).click();await expect(page.getByLabel('授权到期时间',{exact:true})).toHaveValue('2026-12-31T23:30');await expect(page.getByRole('checkbox',{name:/deebee-mcp ·/})).toBeChecked();await save(page);await settled(page);
  await page.getByRole('button',{name:'查看可用权限',exact:true}).click();await settled(page);await choose(page,'系统账号',principalName);await page.getByText('模拟访问凭证的权限范围',{exact:true}).click();await page.getByLabel('模拟凭证权限',{exact:true}).fill('resources:read,ssh:inspect');await page.getByRole('button',{name:'查看可用资源账号',exact:true}).click();await settled(page);await expect(page.locator('.report-content')).toContainText('deebee-mcp');
});

test('API Key 签发、实际只读执行、执行详情、撤销',async({page})=>{
  test.setTimeout(120000);
  await nav(page,'外部身份');await page.getByRole('button',{name:'访问凭证（API Key）',exact:true}).click();await settled(page);await page.getByRole('button',{name:'添加 API Key',exact:true}).click();await page.getByLabel('凭证名称',{exact:true}).fill(`巡检只读凭证 ${suffix}`);await choose(page,'系统账号',principalName);await choose(page,'身份验证服务',`巡检签发服务 ${suffix}`);await page.getByRole('checkbox',{name:'只读检查服务器',exact:true}).locator('..').click();await page.getByRole('checkbox',{name:'Linux 8.159.155.8',exact:true}).locator('..').click();await save(page);await settled(page);
  key=await page.getByLabel('新 API Key',{exact:true}).inputValue();expect(key.length>20).toBeTruthy();await page.getByRole('button',{name:'已保存，隐藏原文',exact:true}).click();await expect(page.getByLabel('新 API Key',{exact:true})).toHaveCount(0);
  const created=await page.request.post(new URL(rootPath+'/api/v1/ssh/inspections',qaUrl).toString(),{headers:{'X-DeeBee-API-Key':key},data:{resource_id:sshId,check:'overview',mode:'normal',idempotency_key:`admin-qa-${suffix}`}});expect(created.ok()).toBeTruthy();const execution=await created.json();
  await expect.poll(async()=>{const r=await page.request.get(new URL(rootPath+'/api/v1/executions/'+execution.execution_id,qaUrl).toString(),{headers:{'X-DeeBee-API-Key':key}});return (await r.json()).status;},{timeout:60000}).toBe('succeeded');
  await nav(page,'执行记录');await row(page,execution.execution_id).getByRole('button',{name:'查看结果',exact:true}).click();await settled(page);await expect(page.getByText(`执行详情 · ${execution.execution_id}`,{exact:true})).toBeVisible();
  await nav(page,'外部身份');await page.getByRole('button',{name:'访问凭证（API Key）',exact:true}).click();await settled(page);await row(page,`巡检只读凭证 ${suffix}`).getByRole('button',{name:'撤销凭证',exact:true}).click();await settled(page);await expect(row(page,`巡检只读凭证 ${suffix}`)).toContainText('已停用');
  const rejected=await page.request.get(new URL(rootPath+'/api/v1/resources',qaUrl).toString(),{headers:{'X-DeeBee-API-Key':key}});expect(rejected.ok()).toBeFalsy();key='';
  await api(page,'/admin/v1/access-grants/'+grantId,'PATCH',{expected_version:(await api(page,'/admin/v1/access-grants')).items.find((g:{id:string})=>g.id===grantId).version,enabled:false});
});

test('审计筛选及详情、录像双协议回放下载、配置检查导入和同步预览',async({page})=>{
  test.setTimeout(120000);
  await nav(page,'审计日志');await choose(page,'入口','管理后台');await page.getByRole('button',{name:'查询',exact:true}).click();await settled(page);await page.getByRole('button',{name:'查看详情',exact:true}).first().click();await settled(page);await expect(page.getByText(/审计详情 · #/)).toBeVisible();
  await nav(page,'会话录像');await expect(page.getByRole('button',{name:'回放',exact:true}).first()).toBeEnabled();
  for(const protocol of ['ssh','rdp']){
    await page.getByLabel('协议',{exact:true}).selectOption(protocol);await page.getByRole('button',{name:'查询',exact:true}).click();await expect(page.locator('.recordings-table tbody tr').first()).toContainText(protocol.toUpperCase());await page.getByRole('button',{name:'回放',exact:true}).first().click();await expect(page.getByRole('button',{name:'暂停',exact:true})).toBeEnabled({timeout:30000});await page.getByRole('button',{name:'暂停',exact:true}).click();await page.getByRole('button',{name:'跳至结尾',exact:true}).click();await expect(page.getByRole('slider',{name:'回放进度'})).toBeEnabled();await page.getByRole('button',{name:'关闭回放',exact:true}).click();
    const download=page.waitForEvent('download');await page.getByRole('button',{name:'下载',exact:true}).first().click();expect((await download).suggestedFilename()).toMatch(/\.(cast|guac)\.gz$/);
  }
  await nav(page,'系统设置');await page.getByRole('button',{name:'导出配置',exact:true}).click();await settled(page);const config=await page.getByLabel('配置 JSON',{exact:true}).inputValue();expect(JSON.parse(config).version).toBe(1);
  await page.getByRole('button',{name:'检查导入内容',exact:true}).click();await settled(page);await expect(page.getByRole('button',{name:'确认导入',exact:true})).toBeVisible();await page.getByRole('button',{name:'确认导入',exact:true}).click();await settled(page);await page.getByRole('button',{name:'检查导入内容',exact:true}).click();await settled(page);await page.getByLabel('配置 JSON',{exact:true}).fill('{"version":1}');await expect(page.getByRole('button',{name:'确认导入',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'检查导入内容',exact:true}).click();await settled(page);await page.getByRole('button',{name:'确认导入',exact:true}).click();await settled(page);await expect(page.locator('.access-alert')).toContainText('配置已更新');
  await page.getByText('连接同步维护',{exact:true}).click();await page.getByRole('button',{name:'查看工作台连接',exact:true}).click();await settled(page);await expect(page.locator('main')).toContainText('100.80.0.19');
});

test('资源新增、数据库类型字段、账号新增编辑及删除确认',async({page})=>{
  await page.getByRole('button',{name:'新增',exact:true}).click();const name=`巡检数据库 ${suffix}`;await page.getByLabel('资源名称',{exact:true}).fill(name);await choose(page,'资源类型','postgresql');await expect(page.getByLabel('端口',{exact:true})).toHaveValue('5432');await expect(page.getByLabel('PostgreSQL Schema',{exact:true})).toBeVisible();await choose(page,'资源类型','k8s');await expect(page.getByLabel('获准 Namespace',{exact:true})).toBeVisible();await choose(page,'资源类型','mysql');await expect(page.getByLabel('端口',{exact:true})).toHaveValue('3306');await page.getByLabel('主机地址',{exact:true}).fill('db01.dev.idmesh.cn');await page.getByLabel('数据库名称',{exact:true}).fill('idmesh');await save(page);await settled(page);
  const resource=(await api(page,'/admin/v1/resources')).items.find((r:{name:string})=>r.name===name);await page.locator('.resource-card').filter({hasText:name}).getByRole('button',{name:'查看详情',exact:true}).click();await settled(page);await page.getByRole('button',{name:'添加资源账号',exact:true}).click();await page.getByLabel('账号别名',{exact:true}).fill('只读核验前登记');await page.getByLabel('登录用户名',{exact:true}).fill('deebee_mcp_reader');await choose(page,'账号级别','高权限账号');await choose(page,'账号级别','普通账号');await page.getByRole('combobox',{name:'登录方式',exact:true}).click();await expect(page.locator('.arco-select-dropdown:visible').getByText('SSH 私钥',{exact:true})).toHaveCount(0);await page.locator('.arco-select-dropdown:visible').getByText('密码',{exact:true}).click();await save(page);await settled(page);await expect(row(page,'deebee_mcp_reader')).toContainText('连接待检查');
  await row(page,'deebee_mcp_reader').getByRole('button',{name:'编辑',exact:true}).click();await page.getByLabel('权限核验说明',{exact:true}).fill('登记真实只读账号，尚未提供凭据，保持禁用。');await save(page);await settled(page);
  await nav(page,'资源管理');await page.locator('.resource-card').filter({hasText:name}).getByRole('button',{name:name+'的更多操作',exact:true}).click();await page.locator('.arco-dropdown-option:visible').getByText('删除资源',{exact:true}).click();await expect(page.getByRole('button',{name:'确认删除',exact:true})).toBeDisabled();await page.getByLabel('确认删除的资源名称',{exact:true}).fill(name);await page.getByRole('button',{name:'确认删除',exact:true}).click();await settled(page);await expect(page.locator('.resource-card').filter({hasText:name})).toHaveCount(0);expect((await api(page,'/admin/v1/accounts')).items.filter((a:{resource_id:string})=>a.resource_id===resource.id)).toHaveLength(0);
});

test('OIDC JWT、在线核验和外部 API Key 使用真实协议检查',async({page})=>{
  const fixturePath=process.env.DEEBEE_ADMIN_QA_IDENTITY;test.skip(!fixturePath,'Optional loopback-only identity protocol fixture.');
  const tokens=JSON.parse(readFileSync(fixturePath!,'utf8'));if(!principalId)principalId=(await api(page,'/admin/v1/principals','POST',{name:principalName,kind:'service',enabled:true})).id;
  for(const mode of ['jwt','introspection','external_http']){
    const source=await api(page,'/admin/v1/identity-sources','POST',{name:`协议巡检 ${mode} ${suffix}`,type:mode==='external_http'?'api_key':'oidc',validation_mode:mode,enabled:true,...(mode==='external_http'?{verify_endpoint:'http://127.0.0.1:18991/verify-key',audiences:['deebee']}:{issuer:'http://127.0.0.1:18991',audiences:['http://127.0.0.1:18080']}),...(mode==='introspection'?{introspection_endpoint:'http://127.0.0.1:18991/introspect',service_client_id:'deebee-lab'}:{}),...(mode!=='jwt'?{service_secret:'lab_verifier_service_secret'}:{})});
    await api(page,'/admin/v1/identity-bindings','POST',{source_id:source.id,subject:'lab_subject',principal_id:principalId,enabled:true});
    await nav(page,'系统设置');await page.getByRole('button',{name:'身份验证服务',exact:true}).click();await settled(page);await row(page,`协议巡检 ${mode} ${suffix}`).getByRole('button',{name:'检查验证服务',exact:true}).click();await page.getByLabel('测试凭证',{exact:true}).fill(mode==='jwt'?tokens.jwt:mode==='introspection'?tokens.opaque:'lab_external_key_test_only');await page.getByRole('button',{name:'检查',exact:true}).click();await settled(page);await expect(page.locator('.report-content')).toContainText(principalName);await page.locator('.arco-modal:visible .arco-modal-close-btn').click();
  }
});

test('系统账号入口真实登录、操作下拉、Linux 检查和数据库查询',async({page})=>{
  test.setTimeout(120000);
  const {randomBytes}=await import('node:crypto');const password=randomBytes(24).toString('base64url');const username=`qa_${suffix}`;
  const human=await api(page,'/admin/v1/principals','POST',{name:`巡检人员 ${suffix}`,kind:'human',username,password,enabled:true});
  const resources=(await api(page,'/admin/v1/resources')).items;const accounts=(await api(page,'/admin/v1/accounts')).items;
  for(const name of ['Linux 8.159.155.8','IDMesh MySQL / idmesh']){const r=resources.find((r:{name:string})=>r.name===name);const a=accounts.find((a:{resource_id:string;tier:string})=>a.resource_id===r.id&&a.tier==='normal');await api(page,'/admin/v1/access-grants','POST',{principal_id:human.id,resource_id:r.id,normal_account_id:a.id,enabled:true,actions:['resources:read',r.type==='ssh'?'ssh:inspect':'db:query']});}
  await page.getByRole('link',{name:'系统账号入口',exact:true}).click();await page.getByLabel('用户名',{exact:true}).fill(username);await page.getByLabel('密码',{exact:true}).fill(password);await page.getByRole('button',{name:'登录',exact:true}).click();await expect(page.getByRole('heading',{name:`巡检人员 ${suffix} 的可用资源`,exact:true})).toBeVisible();
  await page.getByRole('button',{name:/Linux 8.159.155.8/}).click();await choose(page,'操作','只读检查服务器');await choose(page,'检查项目','内存');await page.getByRole('button',{name:'提交执行',exact:true}).click();await expect(page.getByRole('button',{name:'刷新结果',exact:true})).toBeEnabled();await expect.poll(async()=>{await page.getByRole('button',{name:'刷新结果',exact:true}).click();return page.locator('.json-panel pre').innerText();},{timeout:60000}).toContain('"status": "succeeded"');
  await page.getByRole('button',{name:/IDMesh MySQL \/ idmesh/}).click();await choose(page,'操作','查询数据库');await page.getByRole('button',{name:'查看数据库结构',exact:true}).click();await expect(page.locator('.json-panel')).toBeVisible();await page.getByRole('button',{name:'提交执行',exact:true}).click();await expect(page.getByRole('button',{name:'刷新结果',exact:true})).toBeEnabled();await expect.poll(async()=>{await page.getByRole('button',{name:'刷新结果',exact:true}).click();return page.locator('.access-panel .access-panel .json-panel pre').innerText();},{timeout:60000}).toContain('"status": "succeeded"');
  await page.getByRole('button',{name:'退出',exact:true}).click();await expect(page.getByRole('heading',{name:'系统账号登录',exact:true})).toBeVisible();
});
