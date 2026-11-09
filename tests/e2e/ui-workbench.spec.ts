import { expect, test, type Page } from '@playwright/test';
const profile={id:'mysql-ui',driver:'mysql',name:'UI QA',host:'db.example.test',port:3306,user:'reader',default_database:'app',default_schema:'',options:{}};
const columns=[{name:'id',data_type:'BIGINT',nullable:false,default:null,extra:'',comment:''},{name:'name',data_type:'VARCHAR(80)',nullable:false,default:null,extra:'',comment:''}];
async function setup(page:Page,multiple=false){
  const errors:string[]=[],writes:unknown[]=[],previews:unknown[]=[];
  let data=[{id:1,name:'Alpha'},{id:2,name:'Beta'}],session=0;
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>sessionStorage.setItem('deebee_token','ui-test'));
  await page.route('**/api/**',route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname,body=request.method()==='GET'?undefined:request.postDataJSON();
    let response:unknown={ok:true};
    if(path.endsWith('/connections'))response=multiple?[profile,{...profile,id:'mysql-secondary',name:'Second QA'}]:[profile];
    else if(path.endsWith('/databases'))response=[{name:'app',charset:'utf8mb4',collation:''},{name:'archive',charset:'utf8mb4',collation:''}];
    else if(path.endsWith('/objects'))response={tables:[{name:'people',object_type:'BASE TABLE',estimated_rows:2}],views:[],routines:[],triggers:[],events:[]};
    else if(path.endsWith('/catalog'))response={database:'app',tables:[{name:'people',object_type:'BASE TABLE',columns:columns.map(c=>({...c,key:c.name==='id'?'PRI':''}))}],routines:[]};
    else if(path.endsWith('/sessions'))response={id:`session-${++session}`};
    else if(path.endsWith('/query'))response={ok:true,results:[{index:0,kind:'rows',columns,rows:data,row_count:data.length,source:{database:'app',schema:'',table:'people',primaryKey:['id']}}],elapsed_ms:3,autocommit:true};
    else if(path.endsWith('/data/read'))response={columns,primary_key:['id'],rows:data,page:1,page_size:100,total:data.length,limited:false};
    else if(path.endsWith('/data/rows')){writes.push(body);if(request.method()==='PATCH')data=data.map(row=>row.id===body.key.id?{...row,...body.changes}:row);if(request.method()==='DELETE')data=data.filter(row=>row.id!==body.key.id);}
    else if(path.endsWith('/ddl/preview')){previews.push(body.spec);response={statements:['CREATE TABLE demo (id BIGINT)'],dangerous:[]};}
    else if(path.endsWith('/jobs'))response=[];
    return route.fulfill({json:response});
  });
  await page.goto('/');await expect(page.locator('.monaco-editor textarea')).toBeVisible();
  return {errors,writes,previews};
}
async function choose(page:Page,label:string,text:string){await page.getByRole('combobox',{name:label,exact:true}).click();await page.locator('.arco-select-dropdown:visible').getByText(text,{exact:true}).click();}

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
  await page.screenshot({path:'test-results/ui-workbench-desktop.png'});
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
