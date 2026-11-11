import { expect, test, type Page } from '@playwright/test';
const profile={id:'mysql-ui',driver:'mysql',name:'UI QA',host:'db.example.test',port:3306,user:'reader',default_database:'app',default_schema:'',options:{}};
const columns=[{name:'id',data_type:'BIGINT',nullable:false,default:null,extra:'',comment:''},{name:'name',data_type:'VARCHAR(80)',nullable:false,default:null,extra:'',comment:''}];
async function setup(page:Page,multiple=false,fixture?:{columns:typeof columns;rows:Record<string,unknown>[]}){
  const gridColumns=fixture?.columns||columns;
  const errors:string[]=[],writes:unknown[]=[],previews:unknown[]=[];
  let data:Record<string,unknown>[]=fixture?.rows||[{id:1,name:'Alpha'},{id:2,name:'Beta'}],session=0;
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>sessionStorage.setItem('deebee_token','ui-test'));
  await page.route('**/api/**',route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname,body=request.method()==='GET'?undefined:request.postDataJSON();
    let response:unknown={ok:true};
    if(path.endsWith('/connections'))response=multiple?[profile,{...profile,id:'mysql-secondary',name:'Second QA'}]:[profile];
    else if(path.endsWith('/databases'))response=[{name:'app',charset:'utf8mb4',collation:''},{name:'archive',charset:'utf8mb4',collation:''}];
    else if(path.endsWith('/objects'))response={tables:[{name:'people',object_type:'BASE TABLE',estimated_rows:2}],views:[],routines:[],triggers:[],events:[]};
    else if(path.endsWith('/catalog'))response={database:'app',tables:[{name:'people',object_type:'BASE TABLE',columns:gridColumns.map(c=>({...c,key:c.name==='id'?'PRI':''}))}],routines:[]};
    else if(path.endsWith('/sessions'))response={id:`session-${++session}`};
    else if(path.endsWith('/query'))response={ok:true,results:[{index:0,kind:'rows',columns:gridColumns,rows:data,row_count:data.length,source:{database:'app',schema:'',table:'people',primaryKey:['id']}}],elapsed_ms:3,autocommit:true};
    else if(path.endsWith('/data/read'))response={columns:gridColumns,primary_key:['id'],rows:data,page:1,page_size:100,total:data.length,limited:false};
    else if(path.endsWith('/data/rows')){writes.push(body);if(request.method()==='PATCH')data=data.map(row=>row.id===body.key.id?{...row,...body.changes}:row);if(request.method()==='DELETE')data=data.filter(row=>row.id!==body.key.id);}
    else if(path.endsWith('/ddl/preview')){previews.push(body.spec);response={statements:['CREATE TABLE demo (id BIGINT)'],dangerous:[]};}
    else if(path.endsWith('/jobs'))response=[];
    return route.fulfill({json:response});
  });
  await page.goto('/');await expect(page.locator('.monaco-editor textarea')).toBeVisible();
  return {errors,writes,previews};
}
async function expectUniformText(page:Page){
  const mismatches=await page.evaluate(()=>Array.from(document.body.querySelectorAll('*')).filter(element=>
    !element.closest('svg')&&element.checkVisibility()&&Array.from(element.childNodes).some(node=>node.nodeType===Node.TEXT_NODE&&node.textContent?.trim())&&getComputedStyle(element).fontSize!=='12px'
  ).map(element=>({text:element.textContent?.slice(0,60),size:getComputedStyle(element).fontSize})));
  expect(mismatches).toEqual([]);
}
async function choose(page:Page,label:string,text:string){await page.getByRole('combobox',{name:label,exact:true}).click();await page.locator('.arco-select-dropdown:visible').getByText(text,{exact:true}).click();}

for(const width of [1440,900,390])test(`login keeps concise content and 14–28px text at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:900});
  await page.goto('/');await expect(page.getByRole('heading',{name:'登录',exact:true})).toBeVisible();
  const login=page.locator('.branded-login');
  if(width>600)await expect(login.getByText('Connect Everything',{exact:true})).toBeVisible();
  await expect(login).not.toContainText(/连接每一处|掌控每一步|连接配置与凭据|从数据库到远程服务器|一个入口|继续你的工作/);
  await expect(login.getByRole('heading',{name:'登录',exact:true})).toHaveCSS('font-size','28px');
  await expect(page.getByLabel('用户名',{exact:true})).toHaveCSS('font-size','14px');
  await expect(page.getByRole('button',{name:'登录工作台',exact:true})).toHaveCSS('font-size','16px');
  expect(await login.evaluate(element=>element.scrollWidth<=element.clientWidth)).toBeTruthy();
  const textSizes=await login.evaluate(element=>Array.from(element.querySelectorAll('*')).filter(child=>!child.closest('svg')&&child.checkVisibility()&&Array.from(child.childNodes).some(node=>node.nodeType===Node.TEXT_NODE&&node.textContent?.trim())).map(child=>parseFloat(getComputedStyle(child).fontSize)));
  expect(textSizes.every(size=>size>=14&&size<=28)).toBeTruthy();
  await page.screenshot({path:`test-results/ui-login-${width}.png`,animations:'disabled'});
  if(width===1440){
    const password=page.getByLabel('密码',{exact:true});await password.fill('preview-password');
    await page.getByRole('button',{name:'显示密码',exact:true}).click();await expect(password).toHaveAttribute('type','text');
    await page.getByRole('button',{name:'隐藏密码',exact:true}).click();await expect(password).toHaveAttribute('type','password');
    const client=await page.context().newCDPSession(page);await client.send('DOM.enable');await client.send('CSS.enable');
    const {root}=await client.send('DOM.getDocument');
    for(const selector of ['#login-username','#login-password']){
      const {nodeId}=await client.send('DOM.querySelector',{nodeId:root.nodeId,selector});
      await client.send('CSS.forcePseudoState',{nodeId,forcedPseudoClasses:['autofill']});
      const field=page.locator(selector);
      expect(await field.evaluate(element=>element.matches(':autofill'))).toBeTruthy();
      await expect(field).toHaveCSS('-webkit-text-fill-color','rgb(236, 241, 255)');
    }
    for(const focused of [false,true]){
      if(focused)await password.focus();else await page.locator('.login-form-brand').click();
      await expect.poll(async()=>password.evaluate(element=>getComputedStyle(element).boxShadow.includes(getComputedStyle(element.closest('.login-input')!).backgroundColor))).toBeTruthy();
      await page.screenshot({path:`test-results/ui-login-autofill-${focused?'focused':'idle'}.png`,animations:'disabled'});
    }
    await page.route('**/api/auth/login',route=>route.fulfill({status:401,json:{error:{message:'用户名或密码错误'}}}));
    await password.press('Enter');const error=page.getByRole('alert');await expect(error).toContainText('用户名或密码错误');
    await expect(error.locator('.arco-alert-content')).toHaveCSS('font-size','14px');
    await expect(password).toHaveValue('preview-password');
  }
});

test('account menu never logs out on avatar click and clean logout requires confirmation',async({page})=>{
  const {errors}=await setup(page);const cleanups:string[]=[];
  page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/sessions/cleanup'))cleanups.push(request.url())});
  const account=page.getByRole('button',{name:'账户菜单',exact:true});
  await account.click();const menu=page.locator('.account-menu-popup');await expect(menu.locator('.account-menu-header')).toContainText('admin');
  await expect(page.locator('.monaco-editor textarea')).toBeVisible();expect(await page.evaluate(()=>sessionStorage.getItem('deebee_token'))).toBe('ui-test');expect(cleanups).toHaveLength(0);
  await expectUniformText(page);await page.screenshot({path:'test-results/ui-account-menu.png',animations:'disabled'});
  await page.locator('.workbench-brand').click();await expect(menu).not.toBeVisible();
  await account.click();await page.getByRole('menuitem',{name:'退出登录',exact:true}).click();
  const confirm=page.getByRole('dialog',{name:'操作确认',exact:true});await expect(confirm).toContainText('确定退出当前账号吗');
  await confirm.getByRole('button',{name:'取消',exact:true}).click();await expect(confirm).not.toBeVisible();
  await expect(account).toBeVisible();expect(await page.evaluate(()=>sessionStorage.getItem('deebee_token'))).toBe('ui-test');expect(cleanups).toHaveLength(0);
  await account.click();await page.getByRole('menuitem',{name:'退出登录',exact:true}).click();await confirm.getByRole('button',{name:'确认',exact:true}).click();
  await expect(page.getByRole('heading',{name:'登录',exact:true})).toBeVisible();
  expect(await page.evaluate(()=>sessionStorage.getItem('deebee_token'))).toBeNull();expect(cleanups).toHaveLength(1);expect(errors).toEqual([]);
});

test('logout confirmation keeps transaction and unsaved query warnings and cancellation preserves the workspace',async({page})=>{
  const {errors}=await setup(page);const editor=page.locator('.monaco-editor textarea');
  await editor.focus();await editor.press(process.platform==='darwin'?'Meta+A':'Control+A');await page.keyboard.insertText('SELECT 2 AS unsaved_preview;');
  await expect(page.locator('.monaco-editor .view-lines')).toContainText('unsaved_preview');
  const autocommit=page.getByRole('switch',{name:'自动提交',exact:true});await autocommit.click();await expect(autocommit).toHaveAttribute('aria-checked','false');
  await page.getByRole('button',{name:'账户菜单',exact:true}).click();await page.getByRole('menuitem',{name:'退出登录',exact:true}).click();
  const confirm=page.getByRole('dialog',{name:'操作确认',exact:true});await expect(confirm).toContainText('1 个手动事务');await expect(confirm).toContainText('1 个未保存查询草稿');
  await confirm.getByRole('button',{name:'取消',exact:true}).click();await expect(confirm).not.toBeVisible();
  await expect(autocommit).toHaveAttribute('aria-checked','false');await expect(page.locator('.monaco-editor .view-lines')).toContainText('unsaved_preview');expect(await page.evaluate(()=>sessionStorage.getItem('deebee_token'))).toBe('ui-test');expect(errors).toEqual([]);
});

test('record popup edits all typed fields together and keeps drafts on validation or server failure',async({page})=>{
  const names=[['id','BIGINT',false,''],['name','VARCHAR(80)',false,''],['amount','DECIMAL(30,8)',false,''],['counter','BIGINT',false,''],['enabled','BOOLEAN',false,''],['payload','JSON',true,''],['note','TEXT',true,''],['computed','INT',false,'STORED GENERATED'] ] as const;
  const {writes,errors}=await setup(page,false,{columns:names.map(([name,data_type,nullable,extra])=>({name,data_type,nullable,extra,default:null,comment:''})),rows:[{id:1,name:'Alpha',amount:'123.00000001',counter:'9007199254740993',enabled:1,payload:{n:1},note:'before',computed:2}]});
  await page.locator('.query-bar').getByRole('button',{name:'运行',exact:true}).click();
  await page.getByRole('cell',{name:'Alpha',exact:true}).click();
  await page.getByRole('button',{name:'编辑记录',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'编辑记录',exact:true});
  const dialogBounds=await dialog.boundingBox();
  expect(dialogBounds!.y).toBeGreaterThanOrEqual(0);
  expect(dialogBounds!.y+dialogBounds!.height).toBeLessThanOrEqual(page.viewportSize()!.height);
  const label=dialog.locator('.grid-record-label').filter({has:page.getByText('name',{exact:true})});
  await expect.poll(async()=>{const nameBounds=await label.locator('strong').boundingBox(),typeBounds=await label.locator('small').boundingBox();return Math.abs(nameBounds!.y-typeBounds!.y)}).toBeLessThan(2);
  expect((await label.locator('..').boundingBox())!.height).toBeLessThanOrEqual(48);
  await expect(label.locator('strong')).toHaveCSS('font-size','12px');await expect(label.locator('small')).toHaveCSS('font-size','12px');
  await expect(dialog.getByRole('button',{name:'保存记录',exact:true})).toHaveCSS('font-size','12px');
  await expectUniformText(page);
  await page.screenshot({path:'test-results/ui-compact-record.png',animations:'disabled'});
  await expect(dialog.getByLabel('记录字段 id',{exact:true})).toBeDisabled();
  await expect(dialog.getByLabel('记录字段 computed',{exact:true})).toBeDisabled();
  await dialog.getByLabel('记录字段 name',{exact:true}).fill('Whole row');
  await dialog.getByLabel('记录字段 amount',{exact:true}).fill('98765432101234567890.12345678');
  await dialog.getByLabel('记录字段 counter',{exact:true}).fill('9007199254740999');
  await choose(page,'记录字段 enabled','false / 0');
  await dialog.getByLabel('记录字段 payload',{exact:true}).fill('{invalid');
  await dialog.getByRole('button',{name:'保存记录',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('JSON 格式无效');expect(writes).toHaveLength(0);
  await dialog.getByLabel('记录字段 payload',{exact:true}).fill('{"n":2}');
  await dialog.getByLabel('note 设为数据库 NULL',{exact:true}).check();
  await page.route('**/api/data/rows',route=>route.fulfill({status:500,json:{error:{message:'保存失败，请重试'}}}),{times:1});
  await dialog.getByRole('button',{name:'保存记录',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('保存失败');
  await expect(dialog.getByLabel('记录字段 name',{exact:true})).toHaveValue('Whole row');expect(writes).toHaveLength(0);
  await dialog.getByRole('button',{name:'保存记录',exact:true}).click();
  await expect(dialog).not.toBeVisible();
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({key:{id:1},changes:{name:'Whole row',amount:'98765432101234567890.12345678',counter:'9007199254740999',enabled:0,payload:'{"n":2}',note:null}});
  expect(Object.keys((writes[0] as {changes:object}).changes)).not.toContain('computed');
  await page.getByRole('cell',{name:'Whole row',exact:true}).click({button:'right'});
  await page.getByRole('menuitem',{name:'编辑记录…',exact:true}).click();
  await dialog.getByLabel('记录字段 name',{exact:true}).fill('Discard this');
  await dialog.getByRole('button',{name:'取消',exact:true}).click();expect(writes).toHaveLength(1);
  await page.getByRole('cell',{name:'Whole row',exact:true}).click();await page.keyboard.press('Shift+Enter');
  await expect(dialog.getByLabel('记录字段 name',{exact:true})).toHaveValue('Whole row');
  await dialog.getByRole('button',{name:'保存记录',exact:true}).click();expect(writes).toHaveLength(1);
  expect(errors).toEqual([]);
});

const toolbarLabels=['新建连接','新建数据库','新建查询','新建表','视图','函数','触发器','事件','用户权限','备份'];
for(const width of [1920,1536,1366,1280,1024,900])test(`top toolbar fits ${width}px with all actions reachable`,async({page})=>{
  await page.setViewportSize({width,height:1080});const {errors}=await setup(page,true);
  const toolbar=page.getByRole('region',{name:'上下文工具栏'});
  const more=toolbar.getByRole('button',{name:'更多功能',exact:true});
  if(width===1920)await expect(more).toHaveCount(0);
  if(width===900)await expect(more).toBeVisible();
  expect(await toolbar.evaluate(e=>e.scrollWidth<=e.clientWidth)).toBeTruthy();
  const bounds=await toolbar.boundingBox();
  for(const button of await toolbar.getByRole('button').all()) {
    const box=await button.boundingBox();if(box)expect(box.x+box.width).toBeLessThanOrEqual(bounds!.x+bounds!.width+1);
  }
  const visible=await toolbar.locator('.toolbar-actions>.toolbar-action').allTextContents();
  const hidden=toolbarLabels.filter(name=>!visible.includes(name));
  if(hidden.length){
    await expect(more).toBeVisible();await more.click();
    const popup=page.locator('.toolbar-overflow-popup .arco-dropdown:visible');
    expect((await popup.boundingBox())!.width).toBeGreaterThanOrEqual(260);
    for(const name of hidden)await expect(popup.getByText(name,{exact:true})).toBeVisible();
    await expectUniformText(page);
    if(width===900)await page.screenshot({path:'test-results/ui-toolbar-overflow-900.png',animations:'disabled'});
    expect(await popup.evaluate(e=>[e,...e.querySelectorAll('*')].every(el=>el.scrollHeight<=el.clientHeight+1 && el.scrollWidth<=el.clientWidth+1))).toBeTruthy();
    if(hidden.includes('视图'))await popup.getByText('视图',{exact:true}).click();
    else {await more.click();await toolbar.getByRole('button',{name:'视图',exact:true}).click();}
  }else{
    await expect(more).toHaveCount(0);await toolbar.getByRole('button',{name:'视图',exact:true}).click();
  }
  await expect(page.locator('.monaco-editor .view-lines')).toContainText('CREATE OR REPLACE VIEW');
  expect(errors).toEqual([]);
});

test('toolbar responds to resizing and connection menu is wider than its compact trigger',async({page})=>{
  await page.setViewportSize({width:900,height:800});const {errors}=await setup(page,true);
  const more=page.getByRole('button',{name:'更多功能',exact:true});await expect(more).toBeVisible();
  await page.setViewportSize({width:1920,height:1080});await expect(more).toHaveCount(0);
  await page.setViewportSize({width:900,height:800});await expect(more).toBeVisible();
  const current=page.getByRole('combobox',{name:'当前连接',exact:true});await current.click();
  const popup=page.locator('.arco-select-dropdown:visible');
  await expect.poll(async()=>(await popup.boundingBox())!.width).toBeGreaterThanOrEqual(320);
  await expectUniformText(page);
  await page.screenshot({path:'test-results/ui-connection-menu-900.png',animations:'disabled'});
  await popup.getByText('Second QA · MySQL',{exact:true}).click();await expect(current).toContainText('Second QA');
  await expect(page.locator('.connection-state')).not.toContainText('Second QA');
  await page.locator('.connection-state').click();await expect(page.locator('.connection-state.online')).toBeVisible();
  expect(errors).toEqual([]);
});

test('record window and JSON/Text fields resize without losing drafts or hiding save controls',async({page})=>{
  await page.setViewportSize({width:1280,height:800});
  const {writes,errors}=await setup(page,false,{columns:[...columns,{name:'payload',data_type:'JSON',nullable:true,default:null,extra:'',comment:''},{name:'note',data_type:'TEXT',nullable:true,default:null,extra:'',comment:''}],rows:[{id:1,name:'Alpha',payload:'{"n":1}',note:'original'}]});
  await page.locator('.query-bar').getByRole('button',{name:'运行',exact:true}).click();await page.getByRole('cell',{name:'Alpha',exact:true}).click();await page.getByRole('button',{name:'编辑记录',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'编辑记录',exact:true}),handle=dialog.getByRole('button',{name:'调整记录编辑窗口大小',exact:true});
  await expect(dialog).toHaveCSS('font-size','12px');await expect(dialog.getByLabel('记录字段 name',{exact:true})).toHaveCSS('font-size','12px');
  await expect.poll(async()=>Math.round((await dialog.boundingBox())!.width)).toBe(860);
  const initial=await dialog.boundingBox();await handle.focus();await handle.press('ArrowRight');await handle.press('ArrowDown');
  await expect.poll(async()=>(await dialog.boundingBox())!.width).toBe(initial!.width+16);
  await expect.poll(async()=>(await dialog.boundingBox())!.height).toBe(initial!.height+16);
  const grip=await handle.boundingBox();await page.mouse.move(grip!.x+grip!.width/2,grip!.y+grip!.height/2);await page.mouse.down();await page.mouse.move(grip!.x+grip!.width/2+80,grip!.y+grip!.height/2+40,{steps:8});await page.mouse.up();
  const resized=await dialog.boundingBox();expect(resized!.width).toBeGreaterThan(initial!.width+100);expect(resized!.height).toBeGreaterThan(initial!.height+40);
  for(const name of ['payload','note']) {
    const field=dialog.getByLabel('记录字段 '+name,{exact:true});await expect(field).toHaveCSS('resize','both');
    await field.scrollIntoViewIfNeeded();const box=await field.boundingBox();
    await page.mouse.move(box!.x+box!.width-4,box!.y+box!.height-4);await page.mouse.down();await page.mouse.move(box!.x+box!.width-4,box!.y+box!.height+76,{steps:8});await page.mouse.up();
    await expect.poll(async()=>(await field.boundingBox())!.height).toBeGreaterThan(box!.height+40);
    const height=(await field.boundingBox())!.height;await field.fill(name==='payload'?'{"n":2}':'resized text\nsecond line');
    expect((await field.boundingBox())!.height).toBe(height);
  }
  expect((await dialog.boundingBox())!.width).toBe(resized!.width);expect((await dialog.boundingBox())!.height).toBe(resized!.height);
  const save=dialog.getByRole('button',{name:'保存记录',exact:true});const saveBox=await save.boundingBox();expect(saveBox!.y+saveBox!.height).toBeLessThan(800);
  await save.click();await expect(dialog).not.toBeVisible();expect(writes).toHaveLength(1);expect(writes[0]).toMatchObject({changes:{payload:'{"n":2}',note:'resized text\nsecond line'}});expect(errors).toEqual([]);
});

test('Arco tabs switch with the keyboard, context actions save a query, and drawers close',async({page})=>{
  const {errors}=await setup(page);
  await expect.poll(async()=>Math.round((await page.locator('.server-node .node-label').first().boundingBox())?.width||0)).toBeGreaterThan(100);
  const tabs=page.locator('.workspace-tabs');
  await page.getByRole('button',{name:'新建查询标签'}).click();await expect(tabs.getByRole('tab')).toHaveCount(2);
  await tabs.getByRole('tab').last().focus();await page.keyboard.press('ArrowLeft');await expect(tabs.getByRole('tab').first()).toHaveAttribute('aria-selected','true');
  await tabs.getByRole('tab').first().click({button:'right'});
  await page.getByRole('menuitem',{name:'保存查询…',exact:true}).click();
  const prompt=page.getByRole('dialog').filter({hasText:'查询名称'});
  await prompt.getByLabel('查询名称',{exact:true}).fill('Daily check');await prompt.getByRole('button',{name:'确认',exact:true}).click();
  await expect(tabs.getByRole('tab',{name:/Daily check/})).toBeVisible();
  await tabs.getByRole('tab').last().getByRole('button',{name:/关闭/}).click();await expect(tabs.getByRole('tab')).toHaveCount(1);
  await expectUniformText(page);
  await page.screenshot({path:'test-results/ui-workbench-desktop.png',animations:'disabled'});
  await page.getByRole('button',{name:'后台任务',exact:true}).click();await expect(page.getByText('暂无后台任务',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'关闭后台任务',exact:true}).click();await expect(page.getByText('暂无后台任务',{exact:true})).not.toBeVisible();
  expect(errors).toEqual([]);
});

test('Arco selectors change query context and result actions preserve inline editing and column sizing',async({page})=>{
  const {errors,writes}=await setup(page);
  await choose(page,'查询数据库','archive');await expect(page.getByRole('combobox',{name:'查询数据库'})).toContainText('archive');
  await page.locator('.query-bar').getByRole('button',{name:'运行',exact:true}).click();
  const cell=page.getByRole('cell',{name:'Alpha',exact:true});await cell.dblclick();
  const editor=page.getByLabel('编辑 name',{exact:true});await expect(editor).toBeFocused();await editor.fill('Edited');await editor.press('Enter');
  await expect(page.getByRole('cell',{name:'Edited',exact:true})).toBeVisible();expect(writes).toHaveLength(1);
  await page.getByRole('cell',{name:'Edited',exact:true}).click({button:'right'});
  await page.getByRole('menuitem',{name:'复制为',exact:true}).hover();await expect(page.getByRole('menuitem',{name:'JSON',exact:true})).toBeVisible();
  await page.keyboard.press('Escape');await page.getByRole('cell',{name:'Edited',exact:true}).click({button:'right'});
  await page.getByRole('menuitem',{name:'设置当前列宽…',exact:true}).click();
  await page.getByLabel('列宽（24-600 像素）',{exact:true}).fill('72');await page.getByRole('dialog').getByRole('button',{name:'确认',exact:true}).click();
  await expect.poll(async()=>Math.round((await page.getByRole('columnheader').filter({hasText:'name'}).boundingBox())?.width||0)).toBe(72);
  await page.locator('.result-tabs').getByRole('tab',{name:'摘要',exact:true}).click();await expect(page.getByText('执行时间',{exact:true})).toBeVisible();
  expect(errors).toEqual([]);
});

test('Arco tables in the structure designer retain text, checkbox and index changes in DDL requests',async({page})=>{
  const {errors,previews}=await setup(page);
  await page.locator('.toolbar').getByRole('button',{name:'新建表',exact:true}).click();
  await expect(page.locator('.structure-table .arco-table')).toBeVisible();
  await page.getByRole('button',{name:'添加字段',exact:true}).click();
  const row=page.getByRole('row').filter({has:page.getByRole('textbox',{name:'字段名 第 2 行',exact:true})});
  await row.getByRole('textbox').first().fill('display_name');await row.getByRole('textbox').first().blur();
  await row.getByRole('checkbox').first().locator('..').click();await expect(row.getByRole('checkbox').first()).not.toBeChecked();
  await page.locator('.designer-tabs').getByRole('tab',{name:'索引',exact:true}).click();
  await page.getByRole('button',{name:'添加索引',exact:true}).click();
  await page.locator('.structure-table').getByRole('checkbox').locator('..').click();await expect(page.locator('.structure-table').getByRole('checkbox')).toBeChecked();
  await page.getByRole('button',{name:'生成 DDL',exact:true}).click();
  await expect(page.getByText('CREATE TABLE demo (id BIGINT);',{exact:true})).toBeVisible();
  expect(previews).toMatchObject([{columns:[{}, {name:'display_name',nullable:false}],indexes:[{unique:true}]}]);
  expect(errors).toEqual([]);
});

test('Arco uploads accept replacement files without uploading them before confirmation',async({page})=>{
  const {errors}=await setup(page);
  await page.locator('.toolbar').getByRole('button',{name:'新建连接',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'新建服务器连接'});
  await dialog.getByRole('button',{name:'Kubernetes',exact:true}).click();
  await dialog.getByRole('tab',{name:'选项',exact:true}).click();
  await choose(page,'Kubernetes 认证方式','完整 Kubeconfig / 客户端证书');
  const file=dialog.locator('input[type=file]');
  await file.setInputFiles({name:'cluster.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({clusters:[{cluster:{server:'https://first.example.test:6443'}}]}))});
  await expect(dialog.getByLabel('Kubeconfig 内容',{exact:true})).toHaveValue(/first.example.test/);
  await file.setInputFiles({name:'other.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({clusters:[{cluster:{server:'https://second.example.test:6443'}}]}))});
  await expect(dialog.getByLabel('Kubeconfig 内容',{exact:true})).toHaveValue(/second.example.test/);
  expect(errors).toEqual([]);
});


test('inputs and selectors use one white outline through hover and focus and still accept edits',async({page})=>{
  const {errors}=await setup(page,true);
  const assertWhite=async(control:import('@playwright/test').Locator)=>{
    await expect(control).toHaveCSS('background-color','rgb(255, 255, 255)');
    await expect(control).toHaveCSS('border-top-width','1px');
    await expect(control).not.toHaveCSS('border-top-color','rgba(0, 0, 0, 0)');
  };
  await assertWhite(page.getByRole('combobox',{name:'当前连接',exact:true}));
  await expect(page.locator('.profile-switch')).toHaveCSS('border-top-width','0px');
  const search=page.getByLabel('筛选连接资源',{exact:true}),shell=page.locator('.tree-search .arco-input-wrapper');
  await assertWhite(shell);const border=await shell.evaluate(e=>getComputedStyle(e).borderTopColor);
  await search.hover();await assertWhite(shell);await search.focus();await assertWhite(shell);
  await expect(shell).not.toHaveCSS('border-top-color',border);
  await search.fill('people');await expect(page.locator('.object-node').filter({hasText:'people'})).toBeVisible();await search.fill('');
  await page.locator('.category-node').filter({hasText:/^表/}).first().click();
  const filter=page.getByLabel('筛选对象',{exact:true});
  await assertWhite(page.locator('.object-filter .arco-input-wrapper'));
  await filter.fill('missing');await expect(page.locator('.object-table').getByRole('cell',{name:'people',exact:true})).toHaveCount(0);
  await filter.fill('people');await expect(page.locator('.object-table').getByRole('cell',{name:'people',exact:true})).toBeVisible();
  await page.screenshot({path:'test-results/ui-inputs-object-list.png',animations:'disabled'});
  await page.getByRole('button',{name:'新建服务器连接',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'新建服务器连接'});
  const name=dialog.getByLabel('连接名称',{exact:true}),inputShell=name.locator('..');
  await assertWhite(inputShell);await name.fill('White input QA');await expect(name).toHaveValue('White input QA');
  await assertWhite(dialog.getByLabel('服务器端口',{exact:true}).locator('..'));
  await dialog.getByRole('button',{name:'SSH',exact:true}).click();await dialog.getByRole('tab',{name:'选项',exact:true}).click();
  await choose(page,'SSH 认证方式','私钥');
  const key=dialog.getByLabel('SSH 私钥',{exact:true});await assertWhite(key.locator('..'));await key.fill('fixture-key');await expect(key).toHaveValue('fixture-key');
  expect(errors).toEqual([]);
});
