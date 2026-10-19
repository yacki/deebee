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
  await page.getByLabel('协议',{exact:true}).selectOption('ssh');
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
  const stream=instruction('size','0','320','200')+instruction('sync','1000')+
    instruction('rect','0','0','0','320','200')+instruction('cfill','14','0','30','110','210','255')+instruction('sync','2000')+
    instruction('rect','0','0','0','80','80')+instruction('cfill','14','0','220','40','30','255')+instruction('sync','3000');
  await management(page,[{...base,protocol:'rdp',target:'Windows QA',host:'100.80.0.35'}],stream);
  await page.getByRole('button',{name:'回放',exact:true}).click();
  await expect(page.locator('.recording-viewport canvas').first()).toBeVisible();
  await expect(page.getByText('正在加载录像…',{exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'跳至结尾',exact:true}).click();
  await expect.poll(()=>page.locator('.recording-viewport canvas').first().evaluate((canvas:HTMLCanvasElement)=>Array.from(canvas.getContext('2d')!.getImageData(10,10,1,1).data))).toEqual([220,40,30,255]);
  await expect(page.getByLabel('回放进度')).toHaveValue('2000');
  await expect(page.getByRole('alert')).toHaveCount(0);
});
