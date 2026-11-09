import { expect, test, type Page } from '@playwright/test';
declare global { interface Window { deebeePerfSaves: number } }

const count = 7000;
const columns = [{name:'id',data_type:'BIGINT',nullable:false,key:'PRI'}, ...Array.from({length:23},(_,i)=>({name:`field_${i+1}`,data_type:'VARCHAR(255)',nullable:true,key:''}))];
const rows = Array.from({length:count},(_,i)=>Object.fromEntries(columns.map((c,j)=>[c.name,j===0?i+1:`app-${i+1}-${j} ${'value '.repeat(5)}`])));
const profile={id:'mysql-perf',driver:'mysql',name:'Performance QA',host:'db.example.test',port:3306,user:'reader',default_database:'app',default_schema:'',options:{}};
async function setup(page:Page){
  const errors:string[]=[],writes:unknown[]=[];let session=0;
  const data=rows.map(row=>({...row}));
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{
    sessionStorage.setItem('deebee_token','perf-test');
    if(!localStorage.getItem('deebee_workspace:mysql-perf'))localStorage.setItem('deebee_workspace:mysql-perf',JSON.stringify({version:1,activeTabId:'large-query',tabs:[
      {id:'large-query',type:'query',title:'7000 rows',database:'app',schema:'',sql:'SELECT * FROM app',savedSql:'SELECT * FROM app'},
      {id:'small-query',type:'query',title:'Other query',database:'app',schema:'',sql:'SELECT 1',savedSql:'SELECT 1'},
    ]}));
    window.deebeePerfSaves=0;
    const original=Storage.prototype.setItem;
    Storage.prototype.setItem=function(key,value){if(this===localStorage&&key.startsWith('deebee_workspace:'))window.deebeePerfSaves++;original.call(this,key,value);};
  });
  await page.route('**/api/**',route=>{
    const request=route.request(),path=new URL(request.url()).pathname,body=request.method()==='GET'?undefined:request.postDataJSON();
    let response:unknown={ok:true};
    if(path.endsWith('/connections'))response=[profile];
    else if(path.endsWith('/databases'))response=[{name:'app',charset:'utf8mb4',collation:''}];
    else if(path.endsWith('/objects'))response={tables:[{name:'app',object_type:'BASE TABLE',estimated_rows:count}],views:[],routines:[],triggers:[],events:[]};
    else if(path.endsWith('/catalog'))response={database:'app',tables:[{name:'app',object_type:'BASE TABLE',columns}],routines:[]};
    else if(path.endsWith('/sessions'))response={id:`session-${++session}`};
    else if(path.endsWith('/query'))response={ok:true,results:[{index:0,kind:'rows',columns,rows:data,row_count:count}],elapsed_ms:3,autocommit:true};
    else if(path.endsWith('/data/read'))response={columns,rows:data.slice(0,100),primary_key:['id'],page:1,page_size:100,total:data.length,limited:false};
    else if(path.endsWith('/data/rows')){writes.push(body);if(body?.changes)Object.assign(data.find(r=>r.id===body.key.id)!,body.changes);if(request.method()==='DELETE')data.splice(data.findIndex(r=>r.id===body.key.id),1);}
    else if(path.endsWith('/jobs'))response=[];
    return route.fulfill({json:response});
  });
  await page.goto('/');await expect(page.locator('.monaco-editor textarea')).toBeVisible();
  return {errors,writes};
}
async function painted(page:Page){return page.evaluate(()=>new Promise<number>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(performance.now())))));}

test('7000 rows render and switch tabs without mounting the entire result',async({page},info)=>{
  test.setTimeout(120_000);const {errors}=await setup(page);
  const start=await page.evaluate(()=>performance.now());
  await page.locator('.query-bar').getByRole('button',{name:'运行',exact:true}).click();
  await expect(page.locator('.editable-grid-shell [data-row-index="0"]')).toBeVisible({timeout:90_000});
  const queryMs=(await painted(page))-start;
  const mountedRows=await page.locator('.editable-grid-shell [data-row-index]').count();
  const mountedCells=await page.locator('.editable-grid-shell tbody td').count();
  const switches:number[]=[];
  const tabs=page.locator('.workspace-tabs');
  for(let i=0;i<3;i++){
    await tabs.getByRole('tab',{name:/Other query/}).click();await expect(page.locator('.editable-grid-shell')).toHaveCount(0);
    const t=await page.evaluate(()=>performance.now());
    await tabs.getByRole('tab',{name:/7000 rows/}).click();
    await expect(page.locator('.editable-grid-shell [data-row-index="0"]')).toBeVisible();
    switches.push((await painted(page))-t);
  }
  const metrics={rows:count,columns:columns.length,queryMs,mountedRows,mountedCells,switchMs:switches};
  console.log('QUERY_PERFORMANCE '+JSON.stringify(metrics));await info.attach('performance',{body:JSON.stringify(metrics,null,2),contentType:'application/json'});
  expect(errors).toEqual([]);
  if(!process.env.DEEBEE_PERF_BASELINE){expect(mountedRows).toBeLessThan(100);expect(Math.max(...switches)).toBeLessThan(500);}
});

async function run(page:Page){
  await page.locator('.query-bar').getByRole('button',{name:'运行',exact:true}).click();
  await expect(page.locator('[data-row-index="0"]')).toBeVisible();
}
async function menu(page:Page,command:string,index?:number){
  const row=index===undefined?page.locator('.editable-grid-shell [data-row-index]').first():page.locator(`[data-row-index="${index}"]`);
  await row.getByRole('cell').nth(2).click({button:'right'});
  await page.getByRole('menuitem',{name:command,exact:true}).click();
}
async function prompt(page:Page,value:string){
  const dialog=page.getByRole('dialog');await dialog.getByRole('textbox').fill(value);await dialog.getByRole('button',{name:'确认',exact:true}).click();
}

test('virtual results keep the last row, edit keys, jump, selection, heights and frozen columns correct',async({page})=>{
  const {errors,writes}=await setup(page);await run(page);
  const grid=page.locator('.editable-grid-shell');
  await expect(page.getByRole('grid')).toHaveAttribute('aria-rowcount','7001');
  const dimensions=await grid.evaluate(e=>({scroll:e.scrollHeight,row:e.querySelector('[data-row-index]')!.getBoundingClientRect().height}));
  expect(dimensions.row).toBe(30);expect(dimensions.scroll).toBeGreaterThanOrEqual(count*30);
  await grid.evaluate(e=>{e.scrollTop=e.scrollHeight;});
  const last=page.locator('[data-row-index="6999"]');await expect(last).toBeVisible();await expect(last.getByRole('cell').nth(1)).toHaveText('7000');
  await expect(page.locator('[data-row-index="0"]')).toHaveCount(0);
  await last.getByRole('cell').nth(2).dblclick();expect(await last.evaluate(e=>e.getBoundingClientRect().height)).toBe(30);await page.getByLabel('编辑 field_1',{exact:true}).fill('edited-last-row');await page.getByLabel('编辑 field_1',{exact:true}).press('Enter');
  await expect(last.getByRole('cell').nth(2)).toHaveText('edited-last-row');expect(writes).toMatchObject([{key:{id:7000},changes:{field_1:'edited-last-row'}}]);
  await last.getByRole('cell').nth(3).dblclick();await page.getByLabel('编辑 field_2',{exact:true}).fill('saved-before-recycling');
  await grid.evaluate(e=>{e.scrollTop=0;});await expect(page.locator('[data-row-index="0"]')).toBeVisible();
  await expect.poll(()=>writes.length).toBe(2);expect(writes[1]).toMatchObject({key:{id:7000},changes:{field_2:'saved-before-recycling'}});
  await grid.evaluate(e=>{e.scrollTop=e.scrollHeight;});await expect(last).toBeVisible();await expect(last.getByRole('cell').nth(3)).toHaveText('saved-before-recycling');
  await menu(page,'全选',6999);await expect(last).toHaveClass(/selected/);
  await menu(page,'跳转到记录…',6999);await prompt(page,'3500');await expect(page.locator('[data-row-index="3499"]')).toBeVisible();await expect(page.locator('[data-row-index="3499"]')).toHaveClass(/selected/);
  await menu(page,'设置行高…',3499);await prompt(page,'24');
  await menu(page,'跳转到记录…');await prompt(page,'7000');await expect(last).toBeVisible();
  await last.getByRole('cell').nth(2).dblclick();expect(await last.evaluate(e=>e.getBoundingClientRect().height)).toBe(24);await page.getByLabel('编辑 field_1',{exact:true}).press('Escape');
  await menu(page,'设置行高…',6999);await prompt(page,'60');
  await menu(page,'跳转到记录…');await prompt(page,'7000');await expect(last).toBeVisible();expect(await last.evaluate(e=>e.getBoundingClientRect().height)).toBe(60);
  await menu(page,'冻结到当前列',6999);await grid.evaluate(e=>{e.scrollLeft=1000;});
  const header=page.getByRole('columnheader',{name:'field_1',exact:true});const h=await header.boundingBox(),g=await grid.boundingBox();expect(h!.x).toBeCloseTo(g!.x+224,0);expect(h!.y).toBeCloseTo((await page.locator('.grid-header').boundingBox())!.y,0);
  await grid.evaluate(e=>{e.scrollLeft=0;});await menu(page,'取消全选',6999);await expect(last).not.toHaveClass(/selected/);
  await page.setViewportSize({width:1440,height:1000});await expect(last).toBeVisible();
  expect(await grid.locator('[data-row-index]').count()).toBeLessThan(100);expect(errors).toEqual([]);
});

test('query results and loading state do not trigger draft persistence, and SQL drafts still restore',async({page})=>{
  const {errors}=await setup(page);const saves=await page.evaluate(()=>window.deebeePerfSaves);
  await run(page);await painted(page);expect(await page.evaluate(()=>window.deebeePerfSaves)).toBe(saves);
  await page.locator('.monaco-editor textarea').focus();await page.keyboard.press(process.platform==='darwin'?'Meta+End':'Control+End');await page.keyboard.insertText(' -- saved draft');
  await expect.poll(()=>page.evaluate(()=>JSON.parse(localStorage.getItem('deebee_workspace:mysql-perf')!).tabs[0].sql)).toContain('-- saved draft');
  expect(await page.evaluate(()=>localStorage.getItem('deebee_workspace:mysql-perf')!.length)).toBeLessThan(1500);
  await page.reload();await expect(page.locator('.monaco-editor textarea')).toBeVisible();await expect(page.locator('.view-lines')).toContainText('-- saved draft');expect(errors).toEqual([]);
});

test('explicit printing includes every result row and returns to bounded rendering',async({page})=>{
  test.setTimeout(60_000);const {errors}=await setup(page);await run(page);
  await page.evaluate(()=>{window.print=()=>document.body.setAttribute('data-printed-rows',String(document.querySelectorAll('.editable-grid-shell [data-row-index]').length));});
  await page.locator('.db-node').click({button:'right'});await page.getByRole('menuitem',{name:'打印数据库…',exact:true}).click();
  await expect(page.locator('body')).toHaveAttribute('data-printed-rows','7000');await expect.poll(()=>page.locator('.editable-grid-shell [data-row-index]').count()).toBeLessThan(100);expect(errors).toEqual([]);
});

test('solid selection stays on the clicked cell and row during hover, repeated clicks and scrolling',async({page})=>{
  const {errors}=await setup(page);await run(page);
  const grid=page.locator('.editable-grid-shell'),row=page.locator('[data-row-index="4"]'),cell=row.getByRole('cell').nth(2);
  await cell.click();await expect(cell).toHaveAttribute('aria-selected','true');await expect(row).toHaveClass(/selected/);
  const id=await cell.getAttribute('id');expect(id).toBeTruthy();
  const border=await cell.evaluate(e=>getComputedStyle(e).boxShadow);expect(border).toContain('inset');
  await page.locator('[data-row-index="5"]').getByRole('cell').nth(2).hover();await expect(cell).toHaveAttribute('aria-selected','true');
  await cell.click();await expect(cell).toHaveAttribute('aria-selected','true');await expect(row).toHaveClass(/selected/);
  await grid.evaluate(e=>{e.scrollTop=9000;});await expect(row).toHaveCount(0);
  await grid.evaluate(e=>{e.scrollTop=0;});await expect(cell).toHaveAttribute('aria-selected','true');await expect(cell).toHaveAttribute('id',id!);await expect(row).toHaveClass(/selected/);
  await cell.click({button:'right'});await expect(cell).toHaveAttribute('aria-selected','true');await page.keyboard.press('Escape');
  expect(errors).toEqual([]);
});

test('native sticky header stays aligned through horizontal and virtual vertical scrolling',async({page})=>{
  const {errors}=await setup(page);await run(page);
  const grid=page.locator('.editable-grid-shell'),header=page.locator('.grid-header');
  await expect(header).toBeVisible();
  const before=await header.boundingBox(),body=await grid.boundingBox();expect(before!.y).toBe(body!.y);
  await grid.evaluate(e=>{e.scrollTop=4500;e.scrollLeft=1200;});
  await expect.poll(()=>grid.evaluate(e=>e.scrollTop)).toBe(4500);
  await expect.poll(async()=>Math.abs((await page.getByRole('columnheader',{name:'field_8',exact:true}).boundingBox())!.x-(await page.locator('[data-row-index="150"]').getByRole('cell').nth(9).boundingBox())!.x)).toBeLessThan(1);
  expect((await header.boundingBox())!.y).toBe(before!.y);
  const row=await page.locator('[data-row-index="150"]').boundingBox();expect(Math.abs(row!.y-body!.y-before!.height)).toBeLessThanOrEqual(1);
  await page.waitForTimeout(150);expect(await grid.evaluate(e=>e.scrollTop)).toBe(4500);
  await grid.evaluate(e=>{e.scrollTop=90000;});await expect(page.locator('[data-row-index="3000"]')).toBeVisible();
  expect((await header.boundingBox())!.y).toBe(before!.y);expect(await grid.locator('[data-row-index]').count()).toBeLessThan(100);
  expect(errors).toEqual([]);
});

test('header and cells move together before scroll handlers run, including frozen columns',async({page})=>{
  const {errors}=await setup(page);await run(page);
  const grid=page.locator('.editable-grid-shell');
  // A native shared scroller must align immediately, even when the main-thread
  // scroll handler has not run. Waiting for eventual alignment misses the lag.
  const positions=async()=>grid.evaluate(body=>{
    const header=body.closest('.editable-grid-frame')!.querySelector('th[aria-label="field_8"]')!;
    const cell=body.querySelector('[data-row-index="0"] [data-column="field_8"]')!;
    return [0,80,400,1200,2300,900,0].map(left=>{
      body.scrollLeft=left;
      return {left:body.scrollLeft,gap:header.getBoundingClientRect().x-cell.getBoundingClientRect().x};
    });
  });
  for(const sample of await positions())expect(Math.abs(sample.gap)).toBeLessThan(1);
  await menu(page,'冻结到当前列',0);
  for(const sample of await positions())expect(Math.abs(sample.gap)).toBeLessThan(1);
  await grid.evaluate(body=>{body.scrollLeft=1200;});
  const frozenHeader=page.getByRole('columnheader',{name:'field_1',exact:true});
  const frozenCell=page.locator('[data-row-index="0"] [data-column="field_1"]');
  expect((await frozenHeader.boundingBox())!.x).toBe((await frozenCell.boundingBox())!.x);
  await grid.evaluate(body=>{body.scrollLeft=0;});
  await grid.hover();await page.mouse.wheel(600,0);
  await expect.poll(()=>grid.evaluate(body=>body.scrollLeft)).toBeGreaterThan(0);
  const gap=await grid.evaluate(body=>body.querySelector('th[aria-label="field_8"]')!.getBoundingClientRect().x-body.querySelector('[data-row-index="0"] [data-column="field_8"]')!.getBoundingClientRect().x);
  expect(Math.abs(gap)).toBeLessThan(1);expect(errors).toEqual([]);
});

test('cell keyboard navigation and editing preserve focus and the original row key',async({page})=>{
  const {errors,writes}=await setup(page);await run(page);
  const frame=page.getByRole('grid');await page.locator('[data-row-index="0"]').getByRole('cell').nth(1).click();await expect(frame).toBeFocused();
  await page.keyboard.press('ArrowRight');await page.keyboard.press('ArrowDown');
  await expect(page.locator('[data-row-index="1"]').getByRole('cell').nth(2)).toHaveAttribute('aria-selected','true');
  await page.keyboard.press('Control+End');
  const last=page.locator('[data-row-index="6999"]');await expect(last).toBeVisible();await expect(last.getByRole('cell').last()).toHaveAttribute('aria-selected','true');
  await page.keyboard.press('F2');const input=page.getByLabel('编辑 field_23',{exact:true});await expect(input).toBeFocused();await input.fill('keyboard-last-cell');await input.press('Enter');
  await expect(frame).toBeFocused();await expect(last.getByRole('cell').last()).toHaveText('keyboard-last-cell');expect(writes).toMatchObject([{key:{id:7000},changes:{field_23:'keyboard-last-cell'}}]);
  await page.keyboard.press('Shift+Tab');await expect(last.getByRole('cell').nth(23)).toHaveAttribute('aria-selected','true');
  await page.keyboard.press('Control+Home');await expect(page.locator('[data-row-index="0"]').getByRole('cell').nth(1)).toHaveAttribute('aria-selected','true');expect(errors).toEqual([]);
});

test('each query keeps its selected cell and scroll position after switching tabs',async({page})=>{
  const {errors}=await setup(page);await run(page);
  const body=page.locator('.editable-grid-shell');await body.evaluate(e=>{e.scrollTop=9000;});
  const cell=page.locator('[data-row-index="302"] [data-column="field_2"]');await cell.click();
  const position=await body.evaluate(e=>({top:e.scrollTop,left:e.scrollLeft}));
  await page.locator('.workspace-tabs').getByRole('tab',{name:/Other query/}).click();await expect(body).toHaveCount(0);
  await page.locator('.workspace-tabs').getByRole('tab',{name:/7000 rows/}).click();
  await expect(cell).toHaveAttribute('aria-selected','true');expect(await body.evaluate(e=>({top:e.scrollTop,left:e.scrollLeft}))).toEqual(position);expect(errors).toEqual([]);
});

test('row headers support stable single and range selection, and context menus preserve it',async({page})=>{
  const {errors}=await setup(page);await run(page);
  const row=(index:number)=>page.locator(`[data-row-index="${index}"]`);
  await row(0).getByRole('cell').first().click();await row(0).getByRole('cell').first().click();await expect(row(0)).toHaveClass(/selected/);
  await row(3).getByRole('cell').first().click({modifiers:[process.platform==='darwin'?'Meta':'Control']});await expect(row(0)).toHaveClass(/selected/);await expect(row(3)).toHaveClass(/selected/);
  await row(5).getByRole('cell').first().click({modifiers:['Shift']});await expect(page.locator('tr.selected')).toHaveCount(3);
  for(const index of [3,4,5])await expect(row(index)).toHaveClass(/selected/);
  await row(4).getByRole('cell').nth(2).click({button:'right'});await expect(page.locator('tr.selected')).toHaveCount(3);
  await page.getByRole('menuitem',{name:'取消全选',exact:true}).click();await expect(page.locator('tr.selected')).toHaveCount(0);await expect(page.locator('.active-cell')).toHaveCount(0);expect(errors).toEqual([]);
});

test('real wheel input over the fixed header scrolls only the data area',async({page})=>{
  const {errors}=await setup(page);await run(page);
  const body=page.locator('.editable-grid-shell'),header=page.locator('.grid-header'),before=await header.boundingBox();
  await header.hover();await page.mouse.wheel(0,600);await expect.poll(()=>body.evaluate(e=>e.scrollTop)).toBe(600);
  await header.hover();await page.mouse.wheel(400,0);await expect.poll(()=>body.evaluate(e=>e.scrollLeft)).toBe(400);
  expect((await header.boundingBox())!.y).toBe(before!.y);expect(await body.evaluate(e=>getComputedStyle(e).overflowAnchor)).toBe('none');expect(errors).toEqual([]);
});

test('data-tab selection controls deletion of the current row and clears safely after reload',async({page})=>{
  const {errors,writes}=await setup(page);
  await page.locator('.object-node').filter({hasText:'app'}).dblclick();
  const remove=page.locator('.data-toolbar').getByRole('button',{name:'删除',exact:true});await expect(remove).toBeDisabled();
  await page.locator('[data-row-index="0"] [data-column="field_1"]').click();await expect(remove).toBeEnabled();
  await menu(page,'取消全选',0);await expect(remove).toBeDisabled();
  await page.locator('[data-row-index="1"] [data-column="field_1"]').click({button:'right'});await page.keyboard.press('Escape');
  await remove.click();await page.getByRole('dialog').getByRole('button',{name:'确认',exact:true}).click();
  await expect.poll(()=>writes.length).toBe(1);expect(writes[0]).toMatchObject({key:{id:2}});
  await expect(remove).toBeDisabled();await expect(page.locator('.active-cell')).toHaveCount(0);expect(errors).toEqual([]);
});

test('result subtabs stay responsive with 7179 rows and Profile never renders row payloads',async({page},info)=>{
  test.setTimeout(120_000);const {errors}=await setup(page);
  const data=Array.from({length:7179},(_,i)=>({...rows[i%rows.length],id:i+1}));
  await page.route('**/api/sessions/*/query',route=>route.fulfill({json:{ok:true,elapsed_ms:12,autocommit:true,
    results:[{index:0,kind:'rows',columns,rows:data,row_count:7179,truncated:true},{index:1,kind:'mutation',affected_rows:2,last_insert_id:9001}],
    warnings:[{Level:'Warning',Code:1265,Message:'QA warning retained'}],status:{Threads_connected:4,version:'QA server'},
  }}));
  await run(page);const body=page.locator('.editable-grid-shell');
  await body.evaluate(e=>{e.scrollTop=9000;});
  const cell=page.locator('[data-row-index="302"] [data-column="field_2"]');await cell.click();
  const position=await body.evaluate(e=>({top:e.scrollTop,left:e.scrollLeft}));
  const switches:{pane:string;ms:number;characters:number}[]=[];
  for(let round=0;round<2;round++)for(const name of ['消息','摘要','Profile','状态','结果 1 (7179)']){
    const start=await page.evaluate(()=>performance.now());
    await page.locator('.result-tabs').getByRole('tab',{name,exact:true}).click();
    await expect(page.locator('.result-tabs').getByRole('tab',{name,exact:true})).toHaveAttribute('aria-selected','true');
    const ms=(await painted(page))-start,characters=await page.locator('.query-view').evaluate(e=>e.textContent!.length);
    switches.push({pane:name,ms,characters});
    if(!process.env.DEEBEE_PERF_BASELINE){
      expect(characters).toBeLessThan(100_000);
      if(name==='Profile'){
        const details=page.locator('.query-profile');await expect(details).toContainText('7179');await expect(details).toContainText('9001');
        await expect(details).toContainText('QA warning retained');await expect(details).not.toContainText('app-1-1');
        const metadata=JSON.parse((await details.locator('pre').textContent())!);expect(metadata.results[0]).not.toHaveProperty('rows');expect(metadata.results[0].columns).toHaveLength(24);
      }
      if(name==='状态')await expect(page.locator('.query-status')).toContainText('Threads_connected');
      if(name==='摘要')await expect(page.locator('.summary-pane')).toContainText('7181');
    }
  }
  console.log('RESULT_SUBTAB_PERFORMANCE '+JSON.stringify({rows:7179,columns:24,switches}));
  await info.attach('result-subtabs',{body:JSON.stringify(switches,null,2),contentType:'application/json'});
  if(!process.env.DEEBEE_PERF_BASELINE)expect(Math.max(...switches.map(s=>s.ms))).toBeLessThan(500);
  await expect(cell).toHaveAttribute('aria-selected','true');expect(await body.evaluate(e=>({top:e.scrollTop,left:e.scrollLeft}))).toEqual(position);
  expect(errors).toEqual([]);
});

test('execution details follow fresh responses, empty results and errors without stale data',async({page})=>{
 const {errors}=await setup(page);
 let execution=0;
 await page.route('**/api/sessions/*/query',route=>{
  execution++;
  if(execution===3)return route.fulfill({status:400,json:{detail:'QA query failed'}});
  return route.fulfill({json:{ok:true,results:execution===1?[{index:0,kind:'mutation',affected_rows:5,last_insert_id:42}]:[{index:0,kind:'rows',columns,rows:[],row_count:0}],elapsed_ms:execution,autocommit:true,warnings:[],status:{connection_id:execution}}});
 });
 const click=()=>page.locator('.query-bar').getByRole('button',{name:'运行',exact:true}).click();
 const pane=(name:string)=>page.locator('.result-tabs').getByRole('tab',{name,exact:true}).click();
 await pane('Profile');await expect(page.locator('.query-profile')).toContainText('尚未执行查询');
 await click();await expect(page.locator('.query-message')).toContainText('执行完成');await pane('Profile');await expect(page.locator('.query-profile')).toContainText('42');
 await pane('状态');await expect(page.locator('.query-status')).toContainText('"connection_id": 1');
 await click();await expect(page.locator('.result-tabs').getByRole('tab',{name:'结果 1 (0)',exact:true})).toHaveAttribute('aria-selected','true');
 await pane('Profile');const metadata=JSON.parse((await page.locator('.query-profile pre').textContent())!);expect(metadata.results).toHaveLength(1);expect(metadata.results[0].row_count).toBe(0);expect(metadata.results[0]).not.toHaveProperty('last_insert_id');
 await pane('状态');await expect(page.locator('.query-status')).toContainText('"connection_id": 2');
 await click();await expect(page.locator('.query-message.error')).toContainText('QA query failed');expect(errors).toEqual([]);
});

test('array-form query rows preserve column order, full values and editable primary keys',async({page})=>{
 const {errors,writes}=await setup(page);
 const data:unknown[][]=rows.map(row=>columns.map(column=>row[column.name]));
 data[0][1]='中文 😀';data[0][2]=null;data[0][3]=false;data[0][4]={$binary:'00ff',size:2};
 let negotiated=false;
 await page.route('**/api/sessions/*/query',route=>{
  negotiated=route.request().headers()['x-deebee-row-format']==='array';
  return route.fulfill({json:{ok:true,results:[{index:0,kind:'rows',columns,rows:data,row_format:'array',row_count:count}],elapsed_ms:300,autocommit:true}});
 });
 await run(page);expect(negotiated).toBe(true);
 const first=page.locator('[data-row-index="0"]');
 await expect(first.locator('[data-column="field_1"]')).toHaveText('中文 😀');await expect(first.locator('[data-column="field_2"]')).toHaveText('NULL');
 await expect(first.locator('[data-column="field_3"]')).toHaveText('false');await expect(first.locator('[data-column="field_4"]')).toHaveText('{"$binary":"00ff","size":2}');
 const grid=page.locator('.editable-grid-shell');await grid.evaluate(e=>{e.scrollTop=e.scrollHeight;});const last=page.locator('[data-row-index="6999"]');
 await expect(last.locator('[data-column="id"]')).toHaveText('7000');await last.locator('[data-column="field_1"]').dblclick();
 await page.getByLabel('编辑 field_1',{exact:true}).fill('array-row-edit');await page.getByLabel('编辑 field_1',{exact:true}).press('Enter');
 expect(writes).toMatchObject([{key:{id:7000},changes:{field_1:'array-row-edit'}}]);expect(errors).toEqual([]);
});
