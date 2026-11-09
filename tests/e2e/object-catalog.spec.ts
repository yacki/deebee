import {expect,test,type Page} from '@playwright/test';

const tableObjects=Array.from({length:66},(_,index)=>({name:index===0?'com_dict':`core_app_${String(index).padStart(3,'0')}_${'long_name_'.repeat(6)}`,object_type:'BASE TABLE',estimated_rows:76028,data_length:35*1024*1024,index_length:1024*1024,engine:'InnoDB',created_at:'2024-07-09 15:45:10',updated_at:'2026-10-20 11:59:24',collation:'utf8mb4_0900_ai_ci',comment:'系统：这是需要截断并且可以悬停查看完整内容的长注释。'.repeat(12)}));
const category=(page:Page,label='表')=>page.locator('.category-node').filter({has:page.locator('.node-label').getByText(label,{exact:true})});
async function setup(page:Page,driver='mysql'){
 const errors:string[]=[],requests:{path:string;body:unknown}[]=[];let session=0;
 page.on('pageerror',e=>errors.push(e.message));await page.addInitScript(()=>sessionStorage.setItem('deebee_token','catalog-fixture'));
 await page.route('**/api/**',route=>{
  const request=route.request(),path=new URL(request.url()).pathname,body=request.method()==='GET'?undefined:request.postDataJSON();requests.push({path,body});let data:unknown={ok:true};
  if(path.endsWith('/connections'))data=[{id:'catalog-mysql',driver,name:'Catalog QA',host:'db.example.test',port:3306,user:'reader',default_database:'app',default_schema:driver==='postgresql'?'public':'',options:{}}];
  else if(path.endsWith('/databases'))data=[{name:'app',charset:'utf8mb4',collation:'utf8mb4_0900_ai_ci'}];
  else if(path.endsWith('/schemas'))data=[{name:'public'},{name:'audit'}];
  else if(path.endsWith('/objects'))data={tables:tableObjects,views:[{name:'view_qa',object_type:'VIEW',comment:'视图注释'.repeat(40)}],routines:[],triggers:[],events:[]};
  else if(path.endsWith('/catalog'))data={database:'app',tables:[],routines:[]};
  else if(path.endsWith('/sessions'))data={id:`session-${++session}`};
  else if(path.endsWith('/ddl'))data={sql:'CREATE VIEW view_qa AS SELECT 1 AS value;'};
  else if(path.endsWith('/data/read'))data={columns:[{name:'id',data_type:'INT',nullable:false},{name:'name',data_type:'VARCHAR(80)',nullable:true}],primary_key:['id'],rows:[{id:1,name:'Alpha'}],page:1,page_size:100,total:1,limited:false};
  else if(path.endsWith('/jobs'))data=[];
  return route.fulfill({json:data});
 });
 await page.goto('/');await expect(category(page)).toBeVisible();return {errors,requests};
}
async function openCatalog(page:Page){if(!await page.locator('.object-table').isVisible())await category(page).click();await expect(page.locator('.object-table .arco-table')).toBeVisible();}

for(const width of [1024,1920])test(`object information uses one header and aligned property rows at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:1080});const {errors}=await setup(page);
 await page.locator('.object-node').filter({has:page.locator('.node-label').getByText('com_dict',{exact:true})}).click();
 const drawer=page.locator('.object-info-drawer'),details=drawer.locator('.inspector-details'),header=drawer.locator('.arco-drawer-header');
 await expect(details).toBeVisible();await expect(header.getByText('对象信息',{exact:true})).toHaveCount(1);await expect(drawer.getByText('对象信息',{exact:true})).toHaveCount(1);await expect(drawer.getByRole('button')).toHaveCount(1);
 await expect(drawer.getByRole('button',{name:'关闭对象信息',exact:true})).toBeVisible();
 expect(await details.locator('dt').allTextContents()).toEqual(['预计行数','引擎','数据大小','索引大小','排序规则','创建时间','更新时间','注释']);
 await expect(details.locator('dl>div').filter({has:page.getByText('创建时间',{exact:true})}).locator('dd')).toHaveText('2024-07-09 15:45:10');
 await expect(details.locator('dl>div').filter({has:page.getByText('注释',{exact:true})}).locator('dd')).toHaveText(tableObjects[0].comment);
 const geometry=await details.evaluate(e=>[...e.querySelectorAll('dl>div')].map(row=>{
  const label=row.querySelector('dt')!.getBoundingClientRect(),value=row.querySelector('dd')!.getBoundingClientRect();
  return {topDifference:Math.abs(label.top-value.top),gap:value.left-label.right,rightOverflow:value.right-e.getBoundingClientRect().right};
 }));
 for(const row of geometry){expect(row.topDifference).toBeLessThan(1);expect(row.gap).toBeGreaterThan(0);expect(row.rightOverflow).toBeLessThanOrEqual(1);}
 expect(await drawer.locator('.arco-drawer-body').evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);
 await drawer.getByRole('button',{name:'关闭对象信息',exact:true}).click();await expect(details).not.toBeVisible();expect(errors).toEqual([]);
});

for(const viewport of [{width:1024,height:768},{width:1440,height:900},{width:1920,height:1080}])test(`CL01-${viewport.width} long object metadata stays clipped in aligned cells, with independent scrolling`,async({page})=>{
 await page.setViewportSize(viewport);const {errors}=await setup(page);await openCatalog(page);
 const pane=page.locator('.content'),explorer=page.locator('.explorer'),table=page.locator('.object-table'),body=table.locator('.arco-table-body');
 const side=await explorer.boundingBox(),work=await pane.boundingBox(),bounds=await body.boundingBox();expect(work!.x).toBeGreaterThanOrEqual(side!.x+side!.width+5);expect(bounds!.x).toBeGreaterThanOrEqual(work!.x);
 const header=table.getByRole('columnheader',{name:'名称',exact:true}),first=table.locator('tbody tr').first().locator('td').first();expect((await header.boundingBox())!.x).toBeCloseTo((await first.boundingBox())!.x,0);await expect(first).toContainText('com_dict');
 for(const cell of await table.locator('tbody tr').first().locator('.arco-table-cell').all())await expect(cell).toHaveCSS('overflow-x','hidden');
 const row=table.locator('tbody tr').first(),created=await row.locator('td').nth(5).boundingBox(),updated=await row.locator('td').nth(6).boundingBox();expect(created!.x+created!.width).toBeLessThanOrEqual(updated!.x+1);
 await body.hover();await page.mouse.wheel(0,650);await expect.poll(()=>body.evaluate(e=>e.scrollTop)).toBeGreaterThan(300);expect((await header.boundingBox())!.y).toBeLessThan(bounds!.y);
 const overflow=await body.evaluate(e=>e.scrollWidth-e.clientWidth);if(overflow>2){await page.mouse.wheel(650,0);await expect.poll(()=>body.evaluate(e=>e.scrollLeft)).toBeGreaterThan(50);expect(Math.abs((await header.boundingBox())!.x-work!.x)).toBeLessThanOrEqual(1);}
 expect(await page.evaluate(()=>document.body.scrollWidth>innerWidth||document.body.scrollHeight>innerHeight)).toBe(false);
 await page.screenshot({path:`test-results/catalog-layout-${viewport.width}.png`,animations:'disabled'});expect(errors).toEqual([]);
});

test('CL02 dragging object headers resizes real columns and survives tab changes and reload',async({page})=>{
 await setup(page);await openCatalog(page);const table=page.locator('.object-table'),header=table.getByRole('columnheader',{name:'名称',exact:true});
 const before=await header.boundingBox();await page.mouse.move(before!.x+before!.width-2,before!.y+before!.height/2);await page.mouse.down();await page.mouse.move(before!.x+before!.width+90,before!.y+before!.height/2,{steps:12});await page.mouse.up();
 await expect.poll(async()=>Math.round((await header.boundingBox())!.width)).toBeGreaterThan(Math.round(before!.width)+70);
 const width=Math.round((await header.boundingBox())!.width);await category(page,'视图').click();await expect(table.getByRole('cell',{name:'view_qa',exact:true})).toBeVisible();await category(page).click();await expect.poll(async()=>Math.round((await header.boundingBox())!.width)).toBe(width);
 await page.reload();await openCatalog(page);await expect.poll(async()=>Math.round((await header.boundingBox())!.width)).toBe(width);
});

test('CL03 category context menus open lists, create in the right database and refresh; object rows keep their menus',async({page})=>{
 const {errors,requests}=await setup(page);await category(page).click({button:'right'});const menu=page.getByRole('menu',{name:'app · 表',exact:true});await expect(menu).toBeVisible();
 await menu.getByRole('menuitem',{name:'打开对象列表',exact:true}).click();await expect(page.getByRole('cell',{name:'com_dict',exact:true})).toBeVisible();
 await category(page).click({button:'right'});const count=requests.filter(r=>r.path.endsWith('/objects')).length;await page.getByRole('menuitem',{name:'刷新',exact:true}).click();await expect.poll(()=>requests.filter(r=>r.path.endsWith('/objects')).length).toBeGreaterThan(count);
 await page.getByRole('cell',{name:'com_dict',exact:true}).click({button:'right'});await expect(page.getByRole('menuitem',{name:'打开表',exact:true})).toBeVisible();await page.keyboard.press('Escape');
 await page.getByRole('cell',{name:'com_dict',exact:true}).dblclick();await expect(page.getByRole('cell',{name:'Alpha',exact:true})).toBeVisible();expect(requests.findLast(r=>r.path.endsWith('/data/read'))?.body).toMatchObject({database:'app',table:'com_dict'});
 await category(page).click({button:'right'});await page.getByRole('menuitem',{name:'新建表',exact:true}).click();await expect(page.getByLabel('表名',{exact:true})).toBeVisible();await expect(page.locator('.view-header')).toContainText('app');
 expect(errors).toEqual([]);
});

test('CL04 the framework divider resizes, clamps, restores and persists a distinct explorer pane',async({page})=>{
 await page.setViewportSize({width:1440,height:900});await setup(page);await openCatalog(page);const divider=page.getByRole('separator',{name:'调整连接栏宽度',exact:true}),explorer=page.locator('.explorer');
 await expect(explorer).toHaveCSS('background-color','rgb(238, 241, 245)');const before=await explorer.boundingBox(),handle=await divider.boundingBox();await page.mouse.move(handle!.x+3,handle!.y+handle!.height/2);await page.mouse.down();await page.mouse.move(handle!.x+143,handle!.y+handle!.height/2,{steps:12});await page.mouse.up();
 await expect.poll(async()=>Math.round((await explorer.boundingBox())!.width)).toBe(Math.round(before!.width)+140);const saved=Math.round((await explorer.boundingBox())!.width);await page.reload();await expect.poll(async()=>Math.round((await explorer.boundingBox())!.width)).toBe(saved);
 await divider.focus();await page.keyboard.press('Home');await expect(explorer).toHaveCSS('width','220px');await page.keyboard.press('ArrowRight');await expect(explorer).toHaveCSS('width','236px');await divider.dblclick();await expect(explorer).toHaveCSS('width','276px');
 await page.setViewportSize({width:1024,height:768});await divider.focus();await page.keyboard.press('End');await expect(explorer).toHaveCSS('width','504px');expect((await page.locator('.content').boundingBox())!.width).toBeGreaterThan(500);
 await page.screenshot({path:'test-results/catalog-explorer-resized.png',animations:'disabled'});
});


test('CL05 object views open their definition and non-query tabs disable query-only actions',async({page})=>{
 const {errors}=await setup(page);await openCatalog(page);await page.locator('.workspace-tabs').getByRole('tab').filter({has:page.getByText('表',{exact:true})}).click({button:'right'});await expect(page.getByRole('menuitem',{name:'复制标签',exact:true})).toHaveClass(/disabled/);await expect(page.getByRole('menuitem',{name:'保存查询…',exact:true})).toHaveClass(/disabled/);await page.keyboard.press('Escape');
 await category(page,'视图').click();await page.getByRole('cell',{name:'view_qa',exact:true}).dblclick();await expect(page.locator('.view-lines')).toContainText('CREATE VIEW view_qa');expect(errors).toEqual([]);
});

test('CL06 schema category context actions preserve the targeted PostgreSQL schema',async({page})=>{
 const {requests,errors}=await setup(page,'postgresql');await page.locator('.schema-node').filter({hasText:'audit'}).locator('.twisty').click();
 await page.locator('.schema-node').filter({hasText:'public'}).click();await category(page).last().click({button:'right'});await expect(page.getByRole('menu',{name:'app.audit · 表',exact:true})).toBeVisible();await page.getByRole('menuitem',{name:'新建查询',exact:true}).click();await expect.poll(()=>requests.filter(r=>r.path.endsWith('/sessions')).length).toBeGreaterThan(1);expect(requests.findLast(r=>r.path.endsWith('/sessions'))?.body).toMatchObject({database:'app',schema:'audit'});expect(errors).toEqual([]);
});

test('CL07 the final comment column also has a working resize handle',async({page})=>{
 await setup(page);await openCatalog(page);const body=page.locator('.object-table .arco-table-body');await body.evaluate(e=>{e.scrollLeft=e.scrollWidth;});const handle=page.getByRole('separator',{name:'调整对象注释列宽',exact:true});await expect(handle).toBeVisible();const before=Number(await handle.getAttribute('aria-valuenow'));await handle.focus();await page.keyboard.press('ArrowRight');await expect(handle).toHaveAttribute('aria-valuenow',String(before+16));await handle.scrollIntoViewIfNeeded();const h=await handle.boundingBox();await page.mouse.move(h!.x+4,h!.y+8);await page.mouse.down();await page.mouse.move(h!.x-76,h!.y+8,{steps:10});await page.mouse.up();await expect.poll(async()=>Number(await handle.getAttribute('aria-valuenow'))).toBeLessThan(before);await handle.dblclick();await expect(handle).toHaveAttribute('aria-valuenow','360');
});


test('CL08 designer columns resize and form controls stay in their own cells after explorer resizing',async({page})=>{
 await page.setViewportSize({width:1024,height:768});await setup(page);await category(page).click({button:'right'});await page.getByRole('menuitem',{name:'新建表',exact:true}).click();await page.getByRole('button',{name:'添加字段',exact:true}).click();
 const header=page.locator('.structure-table').getByRole('columnheader',{name:'字段名',exact:true});const b=await header.boundingBox();await page.mouse.move(b!.x+b!.width-2,b!.y+12);await page.mouse.down();await page.mouse.move(b!.x+b!.width+60,b!.y+12,{steps:8});await page.mouse.up();await expect.poll(async()=>(await header.boundingBox())!.width).toBeGreaterThan(b!.width+40);
 const input=page.getByRole('textbox',{name:'字段名 第 2 行',exact:true});await input.fill('designer_resize_qa');await input.blur();await expect(input).toHaveValue('designer_resize_qa');const cell=await input.locator('xpath=ancestor::td').boundingBox(),control=await input.boundingBox();expect(control!.x).toBeGreaterThanOrEqual(cell!.x);expect(control!.x+control!.width).toBeLessThanOrEqual(cell!.x+cell!.width+1);
 const divider=page.getByRole('separator',{name:'调整连接栏宽度',exact:true});await divider.focus();await page.keyboard.press('ArrowRight');expect(await page.evaluate(()=>document.body.scrollWidth>innerWidth||document.body.scrollHeight>innerHeight)).toBe(false);await page.screenshot({path:'test-results/catalog-designer-resized.png',animations:'disabled'});
});

test('CL09 long trees and overflowing workspace tabs keep independent scroll areas after resizing',async({page})=>{
 await page.setViewportSize({width:1024,height:768});await setup(page);const tree=page.locator('.connection-tree');await tree.hover();await page.mouse.wheel(0,700);await expect.poll(()=>tree.evaluate(e=>e.scrollTop)).toBeGreaterThan(300);expect(await page.locator('.content').evaluate(e=>e.scrollTop)).toBe(0);await tree.evaluate(e=>{e.scrollTop=0;});
 for(let index=0;index<12;index++)await page.getByRole('button',{name:'新建查询标签',exact:true}).click();await expect(page.locator('.workspace-tabs').getByRole('tab')).toHaveCount(13);
 const divider=page.getByRole('separator',{name:'调整连接栏宽度',exact:true});await divider.focus();await page.keyboard.press('End');const selected=page.locator('.workspace-tabs').getByRole('tab',{selected:true});
 await expect.poll(async()=>{const tab=await selected.boundingBox(),content=await page.locator('.content').boundingBox();return tab!.x>=content!.x&&tab!.x+tab!.width<=content!.x+content!.width;}).toBe(true);
 expect(await page.evaluate(()=>document.body.scrollWidth>innerWidth||document.body.scrollHeight>innerHeight)).toBe(false);
});

test('CL10 every one of the nine object columns responds to a real drag',async({page})=>{
 await page.setViewportSize({width:1440,height:900});await setup(page);await openCatalog(page);
 const headers=page.locator('.object-table .arco-table-header th');await expect(headers).toHaveCount(9);
 for(let index=0;index<9;index++){
  const header=headers.nth(index),handle=header.locator(index===8?'.object-column-handle':'.arco-table-column-handle');await handle.scrollIntoViewIfNeeded();
  const before=(await header.boundingBox())!.width,h=(await handle.boundingBox())!;await page.mouse.move(h.x+h.width/2,h.y+h.height/2);await page.mouse.down();await page.mouse.move(h.x+h.width/2-24,h.y+h.height/2,{steps:8});await page.mouse.up();
  await expect.poll(async()=>(await header.boundingBox())!.width).toBeLessThan(before-12);
 }
});

for(const driver of ['mysql','postgresql','mssql'])test(`compact ${driver} tree keeps every level aligned with usable expand controls`,async({page})=>{
 const {errors}=await setup(page,driver);
 const nodes=[page.locator('.server-node').first(),page.locator('.db-node').first(),...(driver==='mysql'?[]:[page.locator('.schema-node').first()]),page.locator('.category-node').first(),page.locator('.object-node').first()];
 const labels=[];
 for(const node of nodes){
  const icon=await node.locator(':scope > .node-icon').boundingBox();labels.push(icon!.x);
  expect((await node.locator('.twisty').boundingBox())!.width).toBe(26);
 }
 for(let i=1;i<labels.length;i++)expect(Math.abs(labels[i]-labels[i-1]-12)).toBeLessThanOrEqual(1);
 expect((await nodes.at(-1)!.locator('.node-label').boundingBox())!.width).toBeGreaterThan(100);
 const tables=category(page);await tables.locator('.twisty').click();await expect(page.locator('.object-node')).toHaveCount(1);
 await tables.locator('.twisty').click();await expect(page.locator('.object-node').filter({hasText:'com_dict'})).toBeVisible();
 if(driver!=='mysql'){
  await page.locator('.schema-node').first().locator('.twisty').click();await expect(page.locator('.category-node')).toHaveCount(0);
  await page.locator('.schema-node').first().locator('.twisty').click();await expect(tables).toBeVisible();
 }
 await page.locator('.db-node').locator('.twisty').click();await expect(page.locator('.category-node')).toHaveCount(0);
 await page.locator('.db-node').locator('.twisty').click();await expect(tables).toBeVisible();expect(errors).toEqual([]);
});
