import {expect, request, test, type APIRequestContext, type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';

const enabled=Boolean(process.env.DEEBEE_REGRESSION_URL);
const database='deebee_e2e',table='deebee_menu_rows';
const apiBase=(process.env.DEEBEE_API_URL||'http://127.0.0.1:8000/api').replace(/\/$/,'')+'/';
const shortcut=process.platform==='darwin'?'Meta+Enter':'Control+Enter';
let api:APIRequestContext,token='',session='';
const errors=new WeakMap<Page,string[]>();
test.skip(!enabled,'Explicit isolated regression instance required');
test.setTimeout(60000);
async function sql(statement:string){const r=await api.post(`sessions/${session}/query`,{data:{sql:statement,limit:10000}});expect(r.ok(),await r.text()).toBeTruthy();return r.json();}
async function scalar(statement:string){const d=await sql(statement);return d.results[0].rows[0]?Object.values(d.results[0].rows[0])[0]:undefined;}
const node=(page:Page,name=table)=>page.locator('.object-node').filter({has:page.locator('.node-label').filter({hasText:new RegExp('^'+name+'$')})}).first();
async function refresh(page:Page){await page.getByRole('button',{name:'刷新服务器',exact:true}).click();await expect(node(page)).toBeVisible();}
async function menu(page:Page,label:string,name=table){await node(page,name).click({button:'right'});await page.getByRole('menuitem',{name:label,exact:true}).click();}
async function submenu(page:Page,parent:string,child:string,name=table){await node(page,name).click({button:'right'});await page.getByRole('menuitem',{name:parent,exact:true}).hover();await page.getByRole('menuitem',{name:child,exact:true}).click();}
async function confirm(page:Page,value?:string){const d=page.getByRole('dialog').last();if(value!==undefined)await d.getByRole('textbox').fill(value);await d.getByRole('button',{name:'确认',exact:true}).click();}
async function run(page:Page,statement:string){const editor=page.locator('.monaco-editor textarea');await editor.focus();await editor.press(process.platform==='darwin'?'Meta+A':'Control+A');await page.keyboard.insertText(statement);await page.locator('.query-bar').getByRole('button',{name:'运行',exact:true}).click();}
async function openData(page:Page){await node(page).dblclick();await expect(page.locator('.data-view')).toBeVisible();await expect(page.getByRole('cell',{name:'Row 0001',exact:true})).toBeVisible();await expect(page.locator('.inspector-details')).not.toBeVisible();}
async function gridMenu(page:Page,label:string,column='name'){const cell=page.locator('.editable-grid-shell tbody tr').first().locator('td').nth(column==='name'?2:1);await cell.click({button:'right'});await page.getByRole('menuitem',{name:label,exact:true}).click();}
async function choose(page:Page,label:string,text:string){await page.getByRole('combobox',{name:label,exact:true}).click();await expect(page.locator('.arco-select-dropdown:visible')).toHaveCount(1);await page.locator('.arco-select-dropdown:visible').getByText(text,{exact:true}).click();}
async function temporary(name:string,engine='InnoDB'){await sql(`DROP TABLE IF EXISTS \`${name}\`; CREATE TABLE \`${name}\` (id INT PRIMARY KEY,name VARCHAR(100)) ENGINE=${engine}; INSERT INTO \`${name}\` VALUES (1,'seed'),(2,'second');`);}

test.beforeAll(async()=>{
 if(!enabled)return;
 expect(['localhost','127.0.0.1']).toContain(new URL(apiBase).hostname);
 const auth=await request.newContext({baseURL:apiBase});const result=await auth.post('auth/login',{data:{username:'admin',password:'deebee'}});expect(result.ok()).toBeTruthy();token=(await result.json()).token;await auth.dispose();
 api=await request.newContext({baseURL:apiBase,extraHTTPHeaders:{Authorization:'Bearer '+token}});
 const profile=(await(await api.get('connections')).json()).find((p:{id:string})=>p.id==='mysql-default');
 expect(profile.host).toBe('127.0.0.1');expect(profile.port).toBe(13377);
 session=(await(await api.post('sessions',{data:{profile_id:'mysql-default',database,autocommit:true}})).json()).id;
 await sql(`DROP TABLE IF EXISTS \`${table}\`; CREATE TABLE \`${table}\` (id INT PRIMARY KEY,name VARCHAR(120) NOT NULL,note TEXT,amount DECIMAL(12,2),payload JSON,${Array.from({length:10},(_,i)=>`field_${i} VARCHAR(250)`).join(',')});`);
 await sql(`INSERT INTO \`${table}\` (id,name,note,amount,payload) VALUES ${Array.from({length:1207},(_,i)=>`(${i+1},'Row ${String(i+1).padStart(4,'0')}','${'Long text '.repeat(12)}',12.50,JSON_OBJECT('index',${i+1}))`).join(',')}`);
});
test.beforeEach(async({page})=>{errors.set(page,[]);page.on('pageerror',e=>errors.get(page)!.push(e.message));await page.context().grantPermissions(['clipboard-read','clipboard-write']);await page.addInitScript(t=>sessionStorage.setItem('deebee_token',t),token);await page.goto(process.env.DEEBEE_REGRESSION_URL!);await expect(page.locator('.monaco-editor textarea')).toBeVisible();});
test.afterEach(async({page})=>{const id=await page.evaluate(()=>sessionStorage.getItem('deebee_workspace_id')).catch(()=>null);if(id)await api.post('sessions/cleanup',{data:{workspace_id:id}});expect(errors.get(page)||[]).toEqual([]);});
test.afterAll(async()=>{if(!api)return;await api.delete(`sessions/${session}`);await api.dispose();});

for(const viewport of [{width:1440,height:900},{width:1280,height:720},{width:1024,height:768}]){
 test(`G01-${viewport.width} data grid wheel, horizontal scrolling, sticky headers and footer do not overlap`,async({page})=>{
  await page.setViewportSize(viewport);await openData(page);
  const grid=page.locator('.editable-grid-shell'),footer=page.locator('.data-footer');
  const bounds=await grid.boundingBox(),foot=await footer.boundingBox();expect(bounds!.y+bounds!.height).toBeLessThanOrEqual(foot!.y+1);expect(foot!.y+foot!.height).toBeLessThanOrEqual(viewport.height+1);
  await grid.hover();await page.mouse.wheel(0,600);await expect.poll(()=>grid.evaluate(e=>e.scrollTop)).toBeGreaterThan(300);
  await grid.hover();await page.mouse.wheel(600,0);await expect.poll(()=>grid.evaluate(e=>e.scrollLeft)).toBeGreaterThan(400);
  const header=await page.getByRole('columnheader',{name:'note',exact:true}).boundingBox();expect(header!.y).toBeCloseTo((await page.locator('.grid-header').boundingBox())!.y,0);
  const bodyOverflow=await page.evaluate(()=>document.body.scrollWidth>innerWidth||document.body.scrollHeight>innerHeight);expect(bodyOverflow).toBe(false);
  await page.screenshot({path:`test-results/regression-grid-${viewport.width}.png`,animations:'disabled'});
 });
 test(`M01-${viewport.width} table context menu uses the available window, reaches every item and opens nonoverlapping submenus`,async({page})=>{
  await page.setViewportSize(viewport);await node(page).click({button:'right'});
  const menu=page.getByRole('menu',{name:table+' 表操作'});await expect(menu).toBeVisible();
  await expect.poll(async()=>{const b=await menu.boundingBox();return b!.height;}).toBeGreaterThan(500);
  const wrapper=menu.locator('xpath=ancestor::*[contains(@class,"arco-dropdown-list-wrapper")][1]');
  expect(await wrapper.evaluate(e=>e.scrollHeight-e.clientHeight)).toBeLessThanOrEqual(1);
  for(const label of ['打开表','设计表','新建表','删除表','清空表','Truncate 表','复制表','查看权限…','导入向导…','导出向导…','数据生成…','转储 SQL 文件…','维护','复制名称','复制 DDL','重命名…','刷新']){
   const b=await menu.getByRole('menuitem',{name:label,exact:true}).boundingBox();expect(b!.y).toBeGreaterThanOrEqual(0);expect(b!.y+b!.height).toBeLessThanOrEqual(viewport.height+1);
  }
  await menu.getByRole('menuitem',{name:'复制表',exact:true}).hover();const child=page.getByRole('menuitem',{name:'仅结构…',exact:true});await expect(child).toBeVisible();
  const parentBox=await menu.boundingBox(),childBox=await child.boundingBox();expect(childBox!.x>=parentBox!.x+parentBox!.width-1||childBox!.x+childBox!.width<=parentBox!.x+1).toBeTruthy();
  await page.screenshot({path:`test-results/regression-menu-${viewport.width}.png`,animations:'disabled'});
  await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);
 });
}

test('M02 short windows have one working menu scrollbar and edge-positioned result menus stay reachable',async({page})=>{
 await page.setViewportSize({width:1024,height:500});await node(page).click({button:'right'});
 const menu=page.getByRole('menu',{name:table+' 表操作'}),wrapper=menu.locator('xpath=ancestor::*[contains(@class,"arco-dropdown-list-wrapper")][1]');
 await wrapper.hover();await page.mouse.wheel(0,600);await expect.poll(()=>wrapper.evaluate(e=>e.scrollTop)).toBeGreaterThan(100);
 await menu.getByRole('menuitem',{name:'刷新',exact:true}).click();await expect(menu).toHaveCount(0);
 await openData(page);const cell=page.locator('.editable-grid-shell tbody tr').first().locator('td').nth(4);await cell.click({button:'right'});
 const result=page.getByRole('menu',{name:'结果表格操作'});const b=await result.boundingBox();expect(b!.x).toBeGreaterThanOrEqual(0);expect(b!.y).toBeGreaterThanOrEqual(0);expect(b!.x+b!.width).toBeLessThanOrEqual(1024);expect(b!.y+b!.height).toBeLessThanOrEqual(500);
 await page.mouse.click(290,52);await expect(result).toHaveCount(0);
});

test('G02 opening a table sends LIMIT 1000, paging is real, filters and sort change server rows',async({page})=>{
 const reading=page.waitForRequest(r=>r.url().endsWith('/data/read'));await openData(page);expect((await reading).postDataJSON()).toMatchObject({limit:1000,page_size:100});
 await expect(page.locator('.data-footer')).toContainText('1000+');await page.locator('.data-footer .arco-pagination-item-next').click();await expect(page.getByRole('cell',{name:'Row 0101',exact:true})).toBeVisible();
 await choose(page,'筛选字段','name');await page.getByLabel('筛选值',{exact:true}).fill('Row 1207');await page.getByRole('button',{name:'应用',exact:true}).click();await expect(page.getByRole('cell',{name:'Row 1207',exact:true})).toBeVisible();await expect(page.locator('.editable-grid-shell tbody tr')).toHaveCount(1);
 await page.getByLabel('筛选值',{exact:true}).fill('');await page.getByRole('button',{name:'应用',exact:true}).click();await page.getByRole('columnheader',{name:'id',exact:true}).click();await page.getByRole('columnheader',{name:'id',exact:true}).click();await expect(page.locator('.editable-grid-shell tbody tr').first()).toContainText('Row 1207');
});

test('G03 cell editing Enter, blur, Escape, primary key protection and narrow columns persist correctly',async({page})=>{
 await openData(page);const cell=page.getByRole('cell',{name:'Row 0001',exact:true});await cell.dblclick();const editor=page.getByLabel('编辑 name',{exact:true});await editor.fill('Changed by Enter');await editor.press('Enter');await expect(page.getByRole('cell',{name:'Changed by Enter',exact:true})).toBeVisible();expect(await scalar(`SELECT name FROM ${table} WHERE id=1`)).toBe('Changed by Enter');
 await page.getByRole('cell',{name:'Changed by Enter',exact:true}).dblclick();await editor.fill('Discarded');await editor.press('Escape');expect(await scalar(`SELECT name FROM ${table} WHERE id=1`)).toBe('Changed by Enter');
 await page.getByRole('cell',{name:'Changed by Enter',exact:true}).dblclick();await editor.fill('Saved on blur');await page.locator('.data-toolbar').getByRole('button',{name:'刷新',exact:true}).focus();await expect.poll(()=>scalar(`SELECT name FROM ${table} WHERE id=1`)).toBe('Saved on blur');
 await page.locator('.editable-grid-shell tbody tr').first().locator('td').nth(1).dblclick();await expect(page.getByLabel('编辑 id',{exact:true})).toHaveCount(0);
 await gridMenu(page,'设置当前列宽…');await confirm(page,'24');await expect(page.getByRole('columnheader',{name:'name',exact:true})).toHaveCSS('width','24px');await page.reload();await expect(node(page)).toBeVisible();await node(page).dblclick();await expect(page.getByRole('columnheader',{name:'name',exact:true})).toHaveCSS('width','24px');
 await sql(`UPDATE ${table} SET name='Row 0001' WHERE id=1`);
});

test('G04 all result menu selection, sizing, frozen columns and row jump actions change the grid',async({page})=>{
 await openData(page);await page.getByRole('cell',{name:'Row 0001',exact:true}).click();await expect(page.locator('.data-toolbar').getByRole('button',{name:'删除',exact:true})).toBeEnabled();await gridMenu(page,'全选');await expect(page.locator('.editable-grid-shell tbody tr.selected')).toHaveCount(100);await gridMenu(page,'取消全选');await expect(page.locator('.editable-grid-shell tbody tr.selected')).toHaveCount(0);await expect(page.locator('.data-toolbar').getByRole('button',{name:'删除',exact:true})).toBeDisabled();
 await gridMenu(page,'设置行高…');await confirm(page,'24');await expect(page.locator('.editable-grid-shell tbody tr').first()).toHaveCSS('height','24px');await gridMenu(page,'设置行高…');await confirm(page,'60');await expect(page.locator('.editable-grid-shell tbody tr').first()).toHaveCSS('height','60px');
 await gridMenu(page,'冻结到当前列');const grid=page.locator('.editable-grid-shell');await grid.evaluate(e=>{e.scrollLeft=700;e.scrollTop=400;});const header=page.getByRole('columnheader',{name:'name',exact:true}),h=await header.boundingBox(),g=await grid.boundingBox();expect(h!.x).toBeGreaterThanOrEqual(g!.x);expect(h!.y).toBeCloseTo((await page.locator('.grid-header').boundingBox())!.y,0);expect(await header.evaluate(e=>{const b=e.getBoundingClientRect();return document.elementFromPoint(b.x+b.width/2,b.y+b.height/2)?.closest('th')===e;})).toBe(true);
 await grid.evaluate(e=>{e.scrollLeft=0;e.scrollTop=0;});await gridMenu(page,'取消冻结所有列');await expect(page.locator('.editable-grid-shell .frozen')).toHaveCount(0);await gridMenu(page,'自动调整全部列宽');expect(Number(await page.getByRole('separator',{name:'调整 note 列宽',exact:true}).getAttribute('aria-valuenow'))).toBeGreaterThan(180);
 await gridMenu(page,'跳转到记录…');await confirm(page,'80');await expect.poll(()=>grid.evaluate(e=>e.scrollTop)).toBeGreaterThan(1000);
});

for(const format of ['INSERT Statement','UPDATE Statement','Tab Separated Values (Data only)','Tab Separated Values (Field Name only)','Tab Separated Values (Field Name and Data)','JSON']){
 test(`C01 ${format} copies actual table, column and value contents`,async({page})=>{await openData(page);await page.getByRole('cell',{name:'Row 0001',exact:true}).click({button:'right'});await page.getByRole('menuitem',{name:'复制为',exact:true}).hover();await page.getByRole('menuitem',{name:format,exact:true}).click();const copied=await page.evaluate(()=>navigator.clipboard.readText());
  if(format==='JSON')expect(JSON.parse(copied)).toMatchObject({id:1,name:'Row 0001'});
  else if(format.includes('Statement')){expect(copied).toContain('`deebee_e2e`.`'+table+'`');expect(copied).not.toContain('table_name');if(format==='UPDATE Statement')expect(copied).toMatch(/WHERE `id` = 1;/);else expect(copied).toContain('Row 0001');}
  else{if(format.includes('Field Name'))expect(copied).toContain('id\tname\tnote');if(format!=='Tab Separated Values (Field Name only)')expect(copied).toContain('Row 0001');}
 });
}
test('C02 cell, name and DDL clipboard commands copy their real source',async({page})=>{await menu(page,'复制名称');expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe('`deebee_e2e`.`'+table+'`');await menu(page,'复制 DDL');await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toContain('CREATE TABLE `'+table+'`');await openData(page);await gridMenu(page,'复制单元格');expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe('Row 0001');});

for(const mode of ['仅结构…','结构和数据…'])test(`T01 ${mode} creates the requested real table`,async({page})=>{const source='deebee_menu_copy_source',target='deebee_menu_copy';await temporary(source);await sql(`DROP TABLE IF EXISTS ${target}`);await refresh(page);await submenu(page,'复制表',mode,source);await confirm(page,target);await expect(node(page,target)).toBeVisible();expect(Number(await scalar(`SELECT COUNT(*) FROM ${target}`))).toBe(mode==='仅结构…'?0:2);await sql(`DROP TABLE ${source},${target}`);});
for(const [label,action] of [['清空表','empty'],['Truncate 表','truncate'],['删除表','drop']] as const)test(`T02 ${label} executes only after confirmation and refreshes open metadata`,async({page})=>{const name='deebee_menu_'+action;await temporary(name);await refresh(page);await menu(page,label,name);await confirm(page);await expect.poll(()=>scalar(action==='drop'?`SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='${database}' AND TABLE_NAME='${name}'`:`SELECT COUNT(*) FROM ${name}`)).toBe(0);if(action==='drop')await expect(node(page,name)).toHaveCount(0);else await sql(`DROP TABLE ${name}`);});
test('T03 rename updates the actual table and the open data tab',async({page})=>{const source='deebee_menu_rename',target='deebee_menu_renamed';await temporary(source);await sql(`DROP TABLE IF EXISTS ${target}`);await refresh(page);await node(page,source).dblclick();await expect(page.locator('.data-view')).toBeVisible();await menu(page,'重命名…',source);await confirm(page,target);await expect(node(page,target)).toBeVisible();await expect(page.locator('.workspace-tabs').getByRole('tab',{name:target,exact:false})).toBeVisible();expect(Number(await scalar(`SELECT COUNT(*) FROM ${target}`))).toBe(2);await sql(`DROP TABLE ${target}`);});
for(const operation of ['CHECK TABLE','ANALYZE TABLE','OPTIMIZE TABLE','REPAIR TABLE'])test(`T04 ${operation} runs and shows the real maintenance response`,async({page})=>{const name='deebee_menu_maintenance';await temporary(name,'MyISAM');await refresh(page);await submenu(page,'维护',operation,name);await expect(page.locator('.inspector-details')).toContainText(operation.split(' ')[0]);await expect(page.locator('.inspector-details')).toContainText('OK');await sql(`DROP TABLE ${name}`);});

test('S01 SQL multiresults, selected statement, EXPLAIN and errors are rendered correctly',async({page})=>{await run(page,'SELECT 11 AS first_result; SELECT 22 AS second_result;');await expect(page.locator('.result-tabs').getByRole('tab',{name:'结果 1 (1)',exact:true})).toBeVisible();await page.locator('.result-tabs').getByRole('tab',{name:'结果 2 (1)',exact:true}).click();await expect(page.getByRole('cell',{name:'22',exact:true})).toBeVisible();
 await run(page,'SELECT 333 AS selected_result; SELECT 444 AS untouched;');const editor=page.locator('.monaco-editor textarea');await editor.press(process.platform==='darwin'?'Meta+ArrowUp':'Control+Home');await editor.press(shortcut);await expect(page.getByRole('cell',{name:'333',exact:true})).toBeVisible();
 await run(page,`SELECT * FROM ${table} WHERE id=1;`);await page.locator('.query-bar').getByRole('button',{name:'执行计划',exact:true}).click();await expect(page.getByRole('cell',{name:table,exact:true})).toBeVisible();
 await run(page,'SELECT missing_column FROM missing_table;');await expect(page.locator('.query-message.error')).toContainText('missing_table');
});
test('S02 manual SQL rollback and commit change real rows, and cancellation stops a running query',async({page})=>{await page.getByRole('switch',{name:'自动提交',exact:true}).click();await run(page,`UPDATE ${table} SET name='rollback candidate' WHERE id=1;`);await expect(page.getByText('执行完成',{exact:true})).toBeVisible();await page.locator('.query-bar').getByRole('button',{name:'回滚',exact:true}).click();expect(await scalar(`SELECT name FROM ${table} WHERE id=1`)).toBe('Row 0001');
 await run(page,`UPDATE ${table} SET name='commit candidate' WHERE id=1;`);await page.locator('.query-bar').getByRole('button',{name:'提交',exact:true}).click();await expect.poll(()=>scalar(`SELECT name FROM ${table} WHERE id=1`)).toBe('commit candidate');
 await run(page,'SELECT SLEEP(20);');await page.locator('.query-bar').getByRole('button',{name:'停止',exact:true}).click();await expect(page.locator('.statusbar')).not.toContainText('正在执行');await sql(`UPDATE ${table} SET name='Row 0001' WHERE id=1`);
});

test('Q01 query tab copy, save, close right, close others and close invoke their actual actions',async({page})=>{const tabs=page.locator('.workspace-tabs');await tabs.getByRole('tab').first().click({button:'right'});await page.getByRole('menuitem',{name:'复制标签',exact:true}).click();await expect(tabs.getByRole('tab')).toHaveCount(2);await page.getByRole('button',{name:'新建查询标签',exact:true}).click();await expect(tabs.getByRole('tab')).toHaveCount(3);await tabs.getByRole('tab').nth(1).click({button:'right'});await page.getByRole('menuitem',{name:'关闭右侧标签',exact:true}).click();await expect(tabs.getByRole('tab')).toHaveCount(2);await tabs.getByRole('tab').last().click({button:'right'});await page.getByRole('menuitem',{name:'保存查询…',exact:true}).click();await confirm(page,'Menu saved query');await expect(tabs.getByRole('tab',{name:/Menu saved query/})).toBeVisible();await tabs.getByRole('tab').last().click({button:'right'});await page.getByRole('menuitem',{name:'关闭其他标签',exact:true}).click();await expect(tabs.getByRole('tab')).toHaveCount(1);await tabs.getByRole('tab').first().click({button:'right'});await page.getByRole('menuitem',{name:'关闭',exact:true}).click();await expect(tabs.getByRole('tab')).toHaveCount(0);});

for(const [label,format] of [['CSV','csv'],['JSON','json'],['SQL INSERT','sql'],['Excel XLSX','xlsx']] as const)test(`W01 ${label} exports the complete 1207-row table rather than the visible page`,async({page})=>{await menu(page,'导出向导…');await choose(page,'文件格式',label);await page.getByRole('button',{name:'下一步',exact:true}).click();const downloading=page.waitForEvent('download');await page.getByRole('button',{name:'开始执行',exact:true}).click();const file=await downloading;expect(file.suggestedFilename()).toContain('.'+format);const bytes=await readFile((await file.path())!);if(format==='json')expect(JSON.parse(bytes.toString())).toHaveLength(1207);else if(format==='csv')expect(bytes.toString()).toContain('Row 1207');else if(format==='sql'){expect(bytes.toString()).toContain('Row 1207');expect(bytes.toString()).toContain(table);}else {expect(bytes.subarray(0,2).toString()).toBe('PK');expect(Number(execFileSync('backend/.venv/bin/python',['-c','import sys,openpyxl;print(openpyxl.load_workbook(open(sys.argv[1],"rb"),read_only=True).active.max_row)',(await file.path())!],{encoding:'utf8'}).trim())).toBe(1208);}});

test('D01 database open/close, properties, new query, console, search, print and refresh execute',async({page})=>{
 const db=page.locator('.db-node').filter({hasText:database});
 const command=async(label:string)=>{await db.click({button:'right'});await page.getByRole('menuitem',{name:label,exact:true}).click();};
 await command('关闭数据库');await expect(node(page)).toHaveCount(0);await command('打开数据库');await expect(node(page)).toBeVisible();
 await command('编辑数据库…');await expect(page.locator('.inspector-details dt').filter({hasText:'字符集'})).toBeVisible();await page.getByRole('button',{name:'关闭对象信息',exact:true}).click();
 await command('新建查询');await expect(page.locator('.workspace-tabs').getByRole('tab')).toHaveCount(2);await command('控制台');await expect(page.locator('.view-lines')).toContainText('SHOW STATUS');await page.locator('.query-bar').getByRole('button',{name:'运行',exact:true}).click();await expect(page.getByRole('columnheader',{name:'Variable_name',exact:true})).toBeVisible();
 await command('在数据库中查找…');await confirm(page,table);await expect(page.getByRole('dialog').last()).toContainText(table);await page.keyboard.press('Escape');
 await page.evaluate(()=>{Object.defineProperty(window,'print',{value:()=>document.body.setAttribute('data-print-invoked','true')});});await command('打印数据库…');await expect(page.locator('body')).toHaveAttribute('data-print-invoked','true');await command('刷新');await expect(node(page)).toBeVisible();
});
test('D02 new database and typed deletion update the server and object tree',async({page})=>{
 const name='deebee_menu_database';await sql(`DROP DATABASE IF EXISTS ${name}`);await page.locator('.db-node').filter({hasText:database}).click({button:'right'});await page.getByRole('menuitem',{name:'新建数据库…',exact:true}).click();const dialog=page.getByRole('dialog',{name:'新建数据库',exact:true});await dialog.getByLabel('数据库名称',{exact:true}).fill(name);await dialog.getByRole('button',{name:'创建',exact:true}).click();const created=page.locator('.db-node').filter({hasText:name});await expect(created).toBeVisible();await created.click({button:'right'});await page.getByRole('menuitem',{name:'删除数据库',exact:true}).click();await confirm(page,name);await expect(created).toHaveCount(0);expect(Number(await scalar(`SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='${name}'`))).toBe(0);
});
test('P01 server expand/collapse, properties, test, edit, duplicate and new connection forms are usable',async({page})=>{
 const server=page.locator('.server-node.active');const command=async(label:string)=>{await server.click({button:'right'});await page.getByRole('menuitem',{name:label,exact:true}).click();};
 await command('收起服务器');await expect(page.locator('.db-node')).toHaveCount(0);await command('展开服务器');await expect(node(page)).toBeVisible();await command('连接属性…');await expect(page.locator('.inspector-details')).toContainText('127.0.0.1');await page.getByRole('button',{name:'关闭对象信息',exact:true}).click();await command('测试连接');await expect(page.getByText('连接成功',{exact:true})).toBeVisible();
 for(const label of ['编辑连接…','复制连接…','新建连接…']){await command(label);const dialog=page.getByRole('dialog',{name:label==='编辑连接…'?'编辑服务器连接':'新建服务器连接',exact:true});await expect(dialog.getByLabel('连接名称',{exact:true})).toBeVisible();if(label!=='新建连接…')await expect(dialog.getByLabel('服务器主机',{exact:true})).toHaveValue('127.0.0.1');await dialog.getByRole('button',{name:'取消',exact:true}).click();}
 await command('刷新');await expect(node(page)).toBeVisible();
});
for(const kind of ['view','function','procedure','trigger','event'])test(`O01 ${kind} definition, edit SQL, copy, refresh and confirmed delete execute`,async({page})=>{
 const name='deebee_menu_'+kind;
 const creation:Record<string,string>={view:`CREATE VIEW ${name} AS SELECT id,name FROM ${table}`,function:`CREATE FUNCTION ${name}(value INT) RETURNS INT DETERMINISTIC RETURN value*2`,procedure:`CREATE PROCEDURE ${name}() SELECT 1`,trigger:`CREATE TRIGGER ${name} BEFORE INSERT ON ${table} FOR EACH ROW SET NEW.name=TRIM(NEW.name)`,event:`CREATE EVENT ${name} ON SCHEDULE EVERY 1 DAY DO SELECT 1`};
 await sql(`DROP ${kind.toUpperCase()} IF EXISTS ${name};${creation[kind]}`);await refresh(page);
 await menu(page,'打开定义',name);await expect(page.locator('.view-lines')).toContainText(name);await menu(page,'编辑定义 SQL',name);await expect(page.locator('.view-lines')).toContainText(name);await menu(page,'复制名称',name);await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(name);await menu(page,'复制 DDL',name);await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toMatch(new RegExp('CREATE.*'+kind,'is'));await menu(page,'刷新',name);await expect(node(page,name)).toBeVisible();
 if(kind==='event'){await menu(page,'禁用',name);await expect.poll(()=>scalar(`SELECT STATUS FROM information_schema.EVENTS WHERE EVENT_SCHEMA='${database}' AND EVENT_NAME='${name}'`)).toBe('DISABLED');await menu(page,'启用',name);await expect.poll(()=>scalar(`SELECT STATUS FROM information_schema.EVENTS WHERE EVENT_SCHEMA='${database}' AND EVENT_NAME='${name}'`)).toBe('ENABLED');}
 await menu(page,'删除对象',name);await confirm(page);await expect(node(page,name)).toHaveCount(0);
});

test('T05 new table designer saves real fields and applies an ALTER through the UI',async({page})=>{
 const name='deebee_menu_designer';await sql(`DROP TABLE IF EXISTS ${name}`);await menu(page,'新建表');await page.getByLabel('表名',{exact:true}).fill(name);await page.getByRole('button',{name:'添加字段',exact:true}).click();await page.getByRole('textbox',{name:'字段名 第 2 行',exact:true}).fill('display_name');await page.getByRole('textbox',{name:'字段名 第 2 行',exact:true}).blur();await page.getByRole('button',{name:'生成 DDL',exact:true}).click();await expect(page.locator('.ddl-pane')).toContainText('display_name');await page.locator('.designer-view').getByRole('button',{name:'保存',exact:true}).click();await expect(page.getByText('表结构已保存',{exact:true})).toBeVisible();expect(Number(await scalar(`SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='${database}' AND TABLE_NAME='${name}' AND COLUMN_NAME='display_name'`))).toBe(1);
 await page.locator('.designer-tabs').getByRole('tab',{name:/字段/}).click();await page.getByRole('button',{name:'添加字段',exact:true}).click();await page.getByRole('textbox',{name:'字段名 第 3 行',exact:true}).fill('extra_note');await page.getByRole('textbox',{name:'字段名 第 3 行',exact:true}).blur();await page.getByRole('button',{name:'生成 DDL',exact:true}).click();await expect(page.locator('.ddl-pane')).toContainText('ADD COLUMN');await page.locator('.designer-view').getByRole('button',{name:'保存',exact:true}).click();await expect.poll(()=>scalar(`SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='${database}' AND TABLE_NAME='${name}' AND COLUMN_NAME='extra_note'`)).toBe(1);await sql(`DROP TABLE ${name}`);
});
for(const format of ['csv','json','xlsx','sql'])test(`W02 ${format} import uploads the chosen file, completes its background job and persists actual rows`,async({page})=>{
 const name='deebee_menu_import';await temporary(name);await refresh(page);await menu(page,'导入向导…',name);
 const bytes=format==='csv'?Buffer.from('id,name\n7,Imported fixture\n'):format==='json'?Buffer.from(JSON.stringify([{id:7,name:'Imported fixture'}])):format==='sql'?Buffer.from(`INSERT INTO ${name}(id,name) VALUES (7,'Imported fixture');`):execFileSync('backend/.venv/bin/python',['-c',"import openpyxl,io,sys;w=openpyxl.Workbook();s=w.active;s.append(['id','name']);s.append([7,'Imported fixture']);b=io.BytesIO();w.save(b);sys.stdout.buffer.write(b.getvalue())"]);
 await page.locator('.file-drop input[type=file]').setInputFiles({name:'fixture.'+format,mimeType:format==='xlsx'?'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':'text/plain',buffer:bytes});await page.getByRole('button',{name:'下一步',exact:true}).click();await page.getByRole('button',{name:'开始执行',exact:true}).click();await expect.poll(()=>scalar(`SELECT name FROM ${name} WHERE id=7`)).toBe('Imported fixture');await sql(`DROP TABLE ${name}`);
});
test('P02 duplicate, save, edit without replacing the password and delete a real isolated connection',async({page})=>{
 test.skip(!process.env.DEEBEE_REGRESSION_ENV,'Requires disposable database credentials');
 const env=JSON.parse((await readFile(process.env.DEEBEE_REGRESSION_ENV!)).toString());
 await page.locator('.server-node.active').click({button:'right'});await page.getByRole('menuitem',{name:'复制连接…',exact:true}).click();let dialog=page.getByRole('dialog',{name:'新建服务器连接',exact:true});await dialog.getByLabel('连接名称',{exact:true}).fill('Menu copied QA');await dialog.getByLabel('连接密码',{exact:true}).fill(env.DEEBEE_MYSQL_PASSWORD);await dialog.getByRole('button',{name:'保存并连接',exact:true}).click();await expect(page.getByLabel('收起连接 Menu copied QA',{exact:true})).toBeVisible();
 await page.locator('.server-node.active').click({button:'right'});await page.getByRole('menuitem',{name:'编辑连接…',exact:true}).click();dialog=page.getByRole('dialog',{name:'编辑服务器连接',exact:true});await dialog.getByLabel('连接名称',{exact:true}).fill('Menu copied QA edited');await dialog.getByRole('button',{name:'保存并连接',exact:true}).click();await expect(page.getByLabel('收起连接 Menu copied QA edited',{exact:true})).toBeVisible();await page.locator('.server-node.active').click({button:'right'});await page.getByRole('menuitem',{name:'测试连接',exact:true}).click();await expect(page.getByText('连接成功',{exact:true})).toBeVisible();await page.locator('.server-node.active').click({button:'right'});await page.getByRole('menuitem',{name:'删除连接',exact:true}).click();await confirm(page);await expect(page.locator('.server-node').filter({hasText:'Menu copied QA edited'})).toHaveCount(0);expect((await(await api.get('connections')).json()).some((p:{name:string})=>p.name==='Menu copied QA edited')).toBe(false);
});
test('S03 leaving a browser workspace releases its backend query session',async({page})=>{const id=await page.evaluate(()=>sessionStorage.getItem('deebee_workspace_id'));const created=await api.post('sessions',{data:{profile_id:'mysql-default',database,workspace_id:id,autocommit:true}});const sid=(await created.json()).id;await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:false})));await expect.poll(async()=>(await api.get('sessions/'+sid)).status()).toBe(400);});
test('G05 switching tables isolates saved column widths, selection and frozen columns',async({page})=>{
 const other='deebee_menu_grid_other';await temporary(other);await refresh(page);await openData(page);await gridMenu(page,'设置当前列宽…');await confirm(page,'72');await gridMenu(page,'全选');await gridMenu(page,'冻结到当前列');await node(page,other).dblclick();await expect(page.getByRole('columnheader',{name:'name',exact:true})).toHaveCSS('width','180px');await expect(page.locator('.editable-grid-shell tbody tr.selected')).toHaveCount(0);await expect(page.locator('.editable-grid-shell .frozen')).toHaveCount(0);await page.locator('.workspace-tabs').getByRole('tab').filter({hasText:table}).click();await expect(page.getByRole('columnheader',{name:'name',exact:true})).toHaveCSS('width','72px');await sql(`DROP TABLE ${other}`);
});

test('S04 running an explicit SQL selection executes only the selected text',async({page})=>{
 await run(page,'SELECT 101 AS first_statement;\nSELECT 202 AS selected_statement;');
 const editor=page.locator('.monaco-editor textarea');await editor.focus();await editor.press(process.platform==='darwin'?'Meta+ArrowDown':'Control+End');await editor.press('Home');await editor.press('Shift+End');await editor.press(shortcut);
 await expect(page.locator('.result-tabs').getByRole('tab',{name:'结果 1 (1)',exact:true})).toBeVisible();await expect(page.getByRole('cell',{name:'202',exact:true})).toBeVisible();await expect(page.getByRole('cell',{name:'101',exact:true})).toHaveCount(0);await expect(page.locator('.result-tabs').getByRole('tab',{name:/结果 2/})).toHaveCount(0);
});

test('G06 a rejected cell edit restores real data and a subsequent valid edit still saves',async({page})=>{
 await openData(page);
 const cell=()=>page.getByRole('cell',{name:'Row 0001',exact:true});
 await cell().dblclick();const editor=page.getByLabel('编辑 name',{exact:true});
 await editor.fill('X'.repeat(121));
 const rejected=page.waitForResponse(response=>response.url().endsWith('/data/rows')&&response.request().method()==='PATCH');
 await editor.press('Enter');expect((await rejected).status()).toBe(400);
 await expect(page.locator('.arco-message-error')).toBeVisible();await expect(cell()).toBeVisible();
 expect(await scalar(`SELECT name FROM ${table} WHERE id=1`)).toBe('Row 0001');
 await cell().dblclick();await editor.fill('Valid retry after rejection');await editor.press('Enter');
 await expect(page.getByRole('cell',{name:'Valid retry after rejection',exact:true})).toBeVisible();
 expect(await scalar(`SELECT name FROM ${table} WHERE id=1`)).toBe('Valid retry after rejection');
 await page.getByRole('cell',{name:'Valid retry after rejection',exact:true}).dblclick();await editor.fill('Row 0001');await editor.press('Enter');await expect(cell()).toBeVisible();
});

test('P03 server new-database/new-query entries and inactive-connection open execute their real targets',async({page})=>{
 const env=JSON.parse(await readFile(process.env.DEEBEE_REGRESSION_ENV!,'utf8'));
 const copyName='Menu inactive target QA',createdDatabase='deebee_menu_server_database';let copied='';
 await sql(`DROP DATABASE IF EXISTS ${createdDatabase}`);
 const server=page.locator('.server-node').filter({has:page.locator('.node-label b').getByText('MySQL',{exact:true})});
 const command=async(label:string)=>{await server.click({button:'right'});await page.getByRole('menuitem',{name:label,exact:true}).click();};
 try{
  await command('新建数据库…');const d=page.getByRole('dialog',{name:'新建数据库',exact:true});await d.getByLabel('数据库名称',{exact:true}).fill(createdDatabase);await d.getByRole('button',{name:'创建',exact:true}).click();
  await expect(page.locator('.db-node').filter({hasText:createdDatabase})).toBeVisible();expect(Number(await scalar(`SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='${createdDatabase}'`))).toBe(1);
  await command('新建查询');await run(page,'SELECT DATABASE() AS context_database;');await expect(page.getByRole('cell',{name:createdDatabase,exact:true})).toBeVisible();
  const created=await api.post('connections',{data:{driver:'mysql',name:copyName,host:'127.0.0.1',port:13377,user:'root',password:env.DEEBEE_MYSQL_PASSWORD,default_database:database,options:{}}});expect(created.ok()).toBeTruthy();copied=(await created.json()).id;
  await page.reload();await expect(page.locator('.monaco-editor textarea')).toBeVisible();const target=page.locator('.server-node').filter({has:page.locator('.node-label b').getByText(copyName,{exact:true})});
  await target.click({button:'right'});await page.getByRole('menuitem',{name:'连接属性…',exact:true}).click();await expect(page.locator('.inspector-details')).toContainText(copyName);await page.getByRole('button',{name:'关闭对象信息',exact:true}).click();
  await target.click({button:'right'});await page.getByRole('menuitem',{name:'测试连接',exact:true}).click();await expect(page.getByText(copyName+' 连接正常',{exact:false})).toBeVisible();
  await target.click({button:'right'});await page.getByRole('menuitem',{name:'打开连接',exact:true}).click();await expect(target).toHaveClass(/active/);await run(page,'SELECT DATABASE() AS opened_database;');await expect(page.getByRole('cell',{name:database,exact:true})).toBeVisible();
 }finally{if(copied)await api.delete('connections/'+copied);await sql(`DROP DATABASE IF EXISTS ${createdDatabase}`);}
});

test('R01 every SQL-result context action uses the query grid and real single-table metadata',async({page})=>{
 await run(page,`SELECT * FROM ${table} ORDER BY id LIMIT 100;`);await expect(page.getByRole('cell',{name:'Row 0001',exact:true})).toBeVisible();
 for(const format of ['INSERT Statement','UPDATE Statement','Tab Separated Values (Data only)','Tab Separated Values (Field Name only)','Tab Separated Values (Field Name and Data)','JSON']){
  await page.getByRole('cell',{name:'Row 0001',exact:true}).click({button:'right'});await page.getByRole('menuitem',{name:'复制为',exact:true}).hover();await page.getByRole('menuitem',{name:format,exact:true}).click();
  const text=await page.evaluate(()=>navigator.clipboard.readText());if(format==='JSON')expect(JSON.parse(text)).toMatchObject({id:1,name:'Row 0001'});else if(format.includes('Statement')){expect(text).toContain('`deebee_e2e`.`'+table+'`');if(format==='UPDATE Statement')expect(text).toContain('WHERE `id` = 1;');}else if(format.includes('Field Name'))expect(text).toContain('id\tname\tnote');else expect(text).toContain('Row 0001');
 }
 await gridMenu(page,'复制单元格');expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe('Row 0001');
 await gridMenu(page,'全选');await expect(page.locator('.editable-grid-shell tbody tr.selected')).toHaveCount(100);await gridMenu(page,'取消全选');await expect(page.locator('.editable-grid-shell tbody tr.selected')).toHaveCount(0);
 await gridMenu(page,'设置当前列宽…');await confirm(page,'24');await expect(page.getByRole('columnheader',{name:'name',exact:true})).toHaveCSS('width','24px');await gridMenu(page,'自动调整全部列宽');await expect(page.getByRole('columnheader',{name:'name',exact:true})).not.toHaveCSS('width','24px');
 await gridMenu(page,'设置行高…');await confirm(page,'24');await expect(page.locator('.editable-grid-shell tbody tr').first()).toHaveCSS('height','24px');await gridMenu(page,'冻结到当前列');
 const grid=page.locator('.editable-grid-shell');await grid.evaluate(e=>{e.scrollLeft=600;e.scrollTop=500;});const h=page.getByRole('columnheader',{name:'name',exact:true});expect(await h.evaluate(e=>{const b=e.getBoundingClientRect();return document.elementFromPoint(b.x+b.width/2,b.y+b.height/2)?.closest('th')===e;})).toBe(true);
 await grid.evaluate(e=>{e.scrollLeft=0;e.scrollTop=0;});await gridMenu(page,'取消冻结所有列');await expect(page.locator('.editable-grid-shell .frozen')).toHaveCount(0);await gridMenu(page,'跳转到记录…');await confirm(page,'80');await expect.poll(()=>grid.evaluate(e=>e.scrollTop)).toBeGreaterThan(500);
});
