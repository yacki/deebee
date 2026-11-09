import { test, expect, type Page } from '@playwright/test';

const base={id:'a'.repeat(32),protocol:'ssh',actor:'operator',target:'Linux QA',host:'100.80.0.19',username:'yacki',started_at:1792410000,duration:3,status:'completed',raw_bytes:240,stored_bytes:140,error:'',sha256:'abc'};
function instruction(...values:string[]){return values.map((v,i)=>`${Array.from(v).length}.${v}${i===values.length-1?';':','}`).join('');}
async function management(page:Page,items:unknown[],content=''){
  await page.addInitScript(()=>sessionStorage.setItem('deebee_token','recordings-admin-test'));
  const queries:URL[]=[];
  await page.route('**/api/**',async route=>{
    const url=new URL(route.request().url());
    if(url.pathname.endsWith('/content'))return route.fulfill({body:content,contentType:'application/octet-stream'});
    if(url.pathname.endsWith('/session-recordings')){queries.push(url);return route.fulfill({json:{items,total:items.length,storage:{used_bytes:200,quota_bytes:5368709120,session_limit_bytes:134217728,retention_days:30}}});}
    return route.fulfill({json:url.pathname.endsWith('/login-options')?{sources:[]}:url.pathname.endsWith('/status')?{ok:true}:{items:[]}});
  });
  await page.goto('/admin');
  await page.getByRole('navigation',{name:'管理后台导航'}).getByText('会话录像',{exact:true}).click();
  return queries;
}

test('administrator can filter recordings and active sessions cannot be replayed',async({page})=>{
  const queries=await management(page,[{...base,status:'recording'}]);
  await expect(page.getByRole('heading',{name:'会话录像',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'回放',exact:true})).toBeDisabled();
  await page.getByLabel('服务器 / 用户').fill('100.80.0.19');
  await page.getByRole('combobox',{name:'协议',exact:true}).click();
  await page.locator('.arco-select-dropdown:visible').getByText('SSH',{exact:true}).click();
  await page.getByRole('button',{name:'查询',exact:true}).click();
  await expect.poll(()=>queries.at(-1)?.searchParams.get('q')).toBe('100.80.0.19');
  expect(queries.at(-1)?.searchParams.get('protocol')).toBe('ssh');
  await expect(page.getByText('保留 30 天',{exact:false})).toBeVisible();
});

test('SSH recording plays terminal output and supports seeking and resizing',async({page})=>{
  const cast=[{version:2,width:80,height:24},[0,'o','SSH recording ready\r\n'],[1,'r','100x30'],[2,'o','Linux 中文回放成功\r\n']].map(v=>JSON.stringify(v)).join('\n')+'\n';
  await management(page,[base],cast);
  await page.getByRole('button',{name:'回放',exact:true}).click();
  await expect(page.getByRole('region',{name:'录像播放器'})).toBeVisible();
  await page.getByRole('button',{name:'跳至结尾',exact:true}).click();
  await expect(page.locator('.recording-viewport .xterm-rows')).toContainText('Linux 中文回放成功');
  await expect(page.getByLabel('回放速度')).toBeVisible();
  await page.getByRole('button',{name:'关闭回放',exact:true}).click();
  await expect(page.getByRole('region',{name:'录像播放器'})).toHaveCount(0);
});

test('RDP protocol recording renders its actual screen and seeking works',async({page})=>{
  await page.clock.install();
  const stream=instruction('size','0','320','200')+instruction('sync','1000')+
    instruction('rect','0','0','0','320','200')+instruction('cfill','14','0','30','110','210','255')+instruction('sync','2000')+
    instruction('rect','0','0','0','80','80')+instruction('cfill','14','0','220','40','30','255')+instruction('sync','3000');
  await management(page,[{...base,protocol:'rdp',target:'Windows QA',host:'100.80.0.35'}],stream);
  await page.getByRole('button',{name:'回放',exact:true}).click();
  await expect(page.locator('.recording-viewport canvas').first()).toBeVisible();
  await expect(page.getByText('正在加载录像…',{exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'跳至结尾',exact:true}).click();
  await expect.poll(()=>page.locator('.recording-viewport canvas').first().evaluate((canvas:HTMLCanvasElement)=>Array.from(canvas.getContext('2d')!.getImageData(10,10,1,1).data))).toEqual([220,40,30,255]);
  await page.clock.runFor(500);
  await expect(page.getByRole('slider',{name:'回放进度'})).toHaveAttribute('aria-valuenow','2000');
  await expect(page.getByRole('button',{name:'播放',exact:true})).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

async function dragTo(page: Page, fraction: number, hold = false) {
  const slider = page.getByRole('slider', {name:'回放进度'});
  await slider.scrollIntoViewIfNeeded();
  const track = await page.locator('.playback-timeline .arco-slider-track').boundingBox();
  const thumb = await slider.boundingBox();
  if (!track || !thumb) throw new Error('回放进度条不可见');
  await page.mouse.move(thumb.x + thumb.width / 2, thumb.y + thumb.height / 2);
  await page.mouse.down();
  await page.mouse.move(track.x + track.width * fraction, thumb.y + thumb.height / 2, {steps:8});
  if (hold) {
    const before = await slider.getAttribute('aria-valuenow');
    await page.clock.runFor(800);
    await expect(slider).toHaveAttribute('aria-valuenow', before!);
  }
  await page.mouse.up();
  await page.clock.runFor(80);
  await expect(page.getByText('正在定位…',{exact:true})).toHaveCount(0);
}
const longCast = [{version:2,width:80,height:24},[0,'o','BEGIN\r\n'],[5,'o','MIDDLE\r\n'],[12,'r','100x30'],[12,'o','LATER\r\n'],[19,'o','END\r\n']].map(value=>JSON.stringify(value)).join('\n')+'\n';

test('SSH playing drag holds its target, resumes playback and reconstructs earlier output',async({page})=>{
  await page.clock.install();
  await management(page,[{...base,duration:20}],longCast);
  await page.getByRole('button',{name:'回放',exact:true}).click();
  await expect(page.getByRole('button',{name:'暂停',exact:true})).toBeEnabled();
  await dragTo(page,0.75,true);
  await expect(page.getByRole('button',{name:'暂停',exact:true})).toBeEnabled();
  await expect(page.locator('.xterm-rows')).toContainText('LATER');
  await page.getByRole('button',{name:'暂停',exact:true}).click();
  await dragTo(page,0.12);
  await expect(page.getByRole('button',{name:'播放',exact:true})).toBeEnabled();
  await expect(page.locator('.xterm-rows')).toContainText('BEGIN');
  await expect(page.locator('.xterm-rows')).not.toContainText('MIDDLE');
  const position=await page.getByRole('slider',{name:'回放进度'}).getAttribute('aria-valuenow');
  await page.clock.runFor(1000);
  await expect(page.getByRole('slider',{name:'回放进度'})).toHaveAttribute('aria-valuenow',position!);
  await dragTo(page,0.9);await dragTo(page,0.3);await dragTo(page,0.7);
  await expect(page.locator('.xterm-rows')).toContainText('LATER');
  await expect(page.locator('.xterm-rows')).not.toContainText('END');
  await page.getByRole('slider',{name:'回放进度'}).focus();
  await page.keyboard.press('Home');await page.clock.runFor(80);
  await expect(page.getByRole('slider',{name:'回放进度'})).toHaveAttribute('aria-valuenow','0');
});

test('RDP real dragging changes the rendered frame and preserves playing or paused state',async({page})=>{
  await page.clock.install();
  const stream=instruction('size','0','320','200')+instruction('sync','1000')+
    instruction('rect','0','0','0','320','200')+instruction('cfill','14','0','30','110','210','255')+instruction('sync','6000')+
    instruction('rect','0','0','0','320','200')+instruction('cfill','14','0','220','40','30','255')+instruction('sync','13000')+instruction('sync','21000');
  await management(page,[{...base,protocol:'rdp',duration:20}],stream);
  await page.getByRole('button',{name:'回放',exact:true}).click();
  await expect(page.getByRole('button',{name:'暂停',exact:true})).toBeEnabled();
  await dragTo(page,0.75,true);
  await expect(page.getByRole('button',{name:'暂停',exact:true})).toBeEnabled();
  const color=()=>page.locator('.recording-viewport canvas').first().evaluate((canvas:HTMLCanvasElement)=>Array.from(canvas.getContext('2d')!.getImageData(10,10,1,1).data));
  await expect.poll(color).toEqual([220,40,30,255]);
  await page.getByRole('button',{name:'暂停',exact:true}).click();
  await dragTo(page,0.3);
  await expect(page.getByRole('button',{name:'播放',exact:true})).toBeEnabled();
  await expect.poll(color).toEqual([30,110,210,255]);
  await dragTo(page,0.8);await dragTo(page,0.3);
  await expect.poll(color).toEqual([30,110,210,255]);
  await page.getByRole('button',{name:'关闭回放',exact:true}).click();
  await page.getByRole('button',{name:'回放',exact:true}).click();
  await expect(page.getByRole('button',{name:'暂停',exact:true})).toBeEnabled();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('RDP seek waits for decoded image frames before completing and restoring earlier state',async({page})=>{
  await page.addInitScript(()=>{
    const decode=window.createImageBitmap.bind(window);
    Object.defineProperty(window,'createImageBitmap',{value:async(blob:Blob)=>{
      await new Promise(resolve=>setTimeout(resolve,250));
      return decode(blob);
    }});
  });
  const images=await page.evaluate(()=>['#1e6ed2','#dc281e'].map(color=>{
    const canvas=document.createElement('canvas');canvas.width=40;canvas.height=40;
    const context=canvas.getContext('2d')!;context.fillStyle=color;context.fillRect(0,0,40,40);
    return canvas.toDataURL('image/png').split(',')[1]!;
  }));
  const frame=(image:string)=>instruction('img','0','14','0','image/png','0','0')+instruction('blob','0',image)+instruction('end','0');
  const stream=instruction('size','0','40','40')+frame(images[0]!)+instruction('sync','1000')+frame(images[1]!)+instruction('sync','13000')+instruction('sync','21000');
  await management(page,[{...base,protocol:'rdp',duration:20}],stream);
  await page.getByRole('button',{name:'回放',exact:true}).click();
  const color=()=>page.locator('.recording-viewport canvas').first().evaluate((canvas:HTMLCanvasElement)=>Array.from(canvas.getContext('2d')!.getImageData(10,10,1,1).data));
  await expect.poll(color).toEqual([30,110,210,255]);
  await page.getByRole('button',{name:'暂停',exact:true}).click();
  const slider=page.getByRole('slider',{name:'回放进度'});
  for(const key of ['End','Home','End','Home']){
    await slider.focus();await page.keyboard.press(key);
    await expect(slider).toHaveAttribute('aria-valuenow',key==='End'?'20000':'0');
    await expect(page.getByRole('button',{name:'播放',exact:true})).toBeEnabled();
    expect(await color()).toEqual(key==='End'?[220,40,30,255]:[30,110,210,255]);
  }
});

test('damaged recordings show an actionable error and another recording can be opened',async({page})=>{
  await management(page,[base],'{"version":1}\n');
  await page.getByRole('button',{name:'回放',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('不支持的终端录像格式');
  await expect(page.getByRole('button',{name:'播放',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'关闭回放',exact:true}).click();
  await page.route('**/session-recordings/*/content',route=>route.fulfill({body:longCast}));
  await page.getByRole('button',{name:'回放',exact:true}).click();
  await expect(page.getByRole('button',{name:'暂停',exact:true})).toBeEnabled();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('RDP recordings with empty offscreen buffers can snapshot, seek backwards and close without canvas errors',async({page})=>{
  test.setTimeout(15000);const issues:string[]=[];page.on('pageerror',error=>issues.push(error.message));
  const stream=instruction('size','0','80','60')+instruction('size','-1','0','0')+instruction('rect','0','0','0','80','60')+instruction('cfill','14','0','30','110','210','255')+instruction('sync','1000')+instruction('rect','0','0','0','80','60')+instruction('cfill','14','0','220','40','30','255')+instruction('sync','13000')+instruction('sync','21000');
  await management(page,[{...base,protocol:'rdp',duration:20}],stream);await page.getByRole('button',{name:'回放',exact:true}).click();await expect(page.getByRole('button',{name:'暂停',exact:true})).toBeEnabled();await page.getByRole('button',{name:'暂停',exact:true}).click();
  const slider=page.getByRole('slider',{name:'回放进度'});await slider.focus();await page.keyboard.press('End');await expect(page.getByRole('button',{name:'播放',exact:true})).toBeEnabled();await slider.focus();await page.keyboard.press('Home');await expect(page.getByRole('button',{name:'播放',exact:true})).toBeEnabled();await expect.poll(()=>page.locator('.recording-viewport canvas').first().evaluate((c:HTMLCanvasElement)=>Array.from(c.getContext('2d')!.getImageData(10,10,1,1).data))).toEqual([30,110,210,255]);await page.getByRole('button',{name:'关闭回放',exact:true}).click();expect(issues).toEqual([]);
});
