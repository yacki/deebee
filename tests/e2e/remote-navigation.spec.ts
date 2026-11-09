import { expect, test, type Page } from '@playwright/test';

const profiles = [
  {id:'mysql-nav',driver:'mysql',name:'Database QA',host:'db.example.test',port:3306,user:'reader',default_database:'app',default_schema:'',options:{}},
  {id:'ssh-nav',driver:'ssh',name:'Linux Server QA',host:'ssh.example.test',port:22,user:'operator',default_database:'',default_schema:'',options:{strict_host_key:true,host_key_fingerprint:'SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG'}},
  {id:'rdp-nav',driver:'rdp',name:'Windows Server QA',host:'rdp.example.test',port:3389,user:'operator',default_database:'',default_schema:'',options:{}},
  {id:'k8s-nav',driver:'k8s',name:'Cluster QA',host:'cluster.example.test',port:443,user:'',default_database:'',default_schema:'',options:{}},
];
const serverTab = (page: Page, name: string) => page.locator('.workspace-tabs').getByRole('tab').filter({has:page.getByText(name,{exact:true})});
const instruction = (...values: string[]) => values.map(value => `${Array.from(value).length}.${value}`).join(',') + ';';
async function setup(page: Page, untrusted = false) {
  const saved = structuredClone(profiles), sockets: string[] = [], errors: string[] = [];
  if (untrusted) saved[1].options.host_key_fingerprint = '';
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => sessionStorage.setItem('deebee_token','navigation-test'));
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let data: unknown = {ok:true,latency_ms:5};
    if (path.endsWith('/connections/test')) data = {latency_ms:5,host_key_new:true,host_key_fingerprint:profiles[1].options.host_key_fingerprint};
    else if (path.endsWith('/connections')) data = saved;
    else if (request.method() === 'PATCH') {
      const target = saved.find(item => path.endsWith('/'+item.id))!;
      Object.assign(target, request.postDataJSON()); data = target;
    }
    else if (path.endsWith('/databases')) data = [{name:'app',charset:'utf8mb4',collation:''}];
    else if (path.endsWith('/objects')) data = {tables:[{name:'people',object_type:'BASE TABLE'}],views:[],routines:[],triggers:[],events:[]};
    else if (path.endsWith('/catalog')) data = {database:'app',tables:[],routines:[]};
    else if (path.endsWith('/sessions')) data = {id:'navigation-session'};
    else if (path.endsWith('/jobs')) data = [];
    return route.fulfill({json:data});
  });
  await page.routeWebSocket('**/api/connections/*/ssh*', socket => {
    sockets.push('ssh'); socket.onMessage(() => {});
    socket.send(JSON.stringify({type:'state',state:'connected'}));
  });
  await page.routeWebSocket('**/api/connections/*/rdp*', socket => {
    sockets.push('rdp'); socket.onMessage(() => {});
    socket.send(instruction('', `connection-${sockets.length}`));
    socket.send(instruction('size','0','800','600'));
    socket.send(instruction('sync','100')); socket.send(instruction('sync','123'));
  });
  await page.goto('/'); await expect(page.locator('.db-node')).toBeVisible();
  return {sockets, errors};
}

for (const driver of ['ssh','rdp'] as const) {
  test(`${driver} is a leaf, connects once per double-click and names each tab after its server`, async ({page}) => {
    const {sockets,errors} = await setup(page), name = profiles.find(item => item.driver === driver)!.name;
    const server = page.getByRole('treeitem',{name:`打开连接 ${name}`,exact:true});
    await server.click();
    await expect(server).not.toHaveAttribute('aria-expanded');
    await expect(server.locator('.twisty svg')).toHaveCount(0);
    await expect(page.locator('.remote-category-node,.remote-session-node')).toHaveCount(0);
    await expect(server).toHaveClass(/active/);
    expect(sockets).toEqual([]);
    await server.dblclick();
    await expect.poll(() => sockets).toEqual([driver]);
    await expect(serverTab(page,name)).toHaveAttribute('aria-selected','true');
    await expect(page.locator(`.${driver}-session .remote-toolbar`)).toContainText(name);
    await page.locator('.toolbar').getByRole('button',{name:driver === 'ssh'?'新建终端':'新建桌面',exact:true}).click();
    await expect.poll(() => sockets.length).toBe(2);
    await expect(serverTab(page,`${name} (2)`)).toHaveAttribute('aria-selected','true');
    await page.getByRole('treeitem',{name:'打开连接 Database QA',exact:true}).click();
    await expect(page.locator('.db-node')).toBeVisible();
    await expect(page.locator('.object-node').filter({hasText:'people'})).toBeVisible();
    await server.dblclick();
    await expect.poll(() => sockets.length).toBe(3);
    await expect(serverTab(page,`${name} (3)`)).toHaveAttribute('aria-selected','true');
    await expect(page.locator('.connection-tree .tree-children,.remote-category-node,.remote-session-node')).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}

test('double-clicking an untrusted SSH server still requires explicit trust before connecting', async ({page}) => {
  const {sockets,errors} = await setup(page,true);
  await page.getByRole('treeitem',{name:'打开连接 Linux Server QA',exact:true}).dblclick();
  const dialog = page.getByRole('dialog',{name:'编辑服务器连接'});
  await expect(dialog.getByRole('group',{name:'确认 SSH 主机密钥'})).toBeVisible();
  expect(sockets).toEqual([]);
  await dialog.getByRole('button',{name:'信任并保存连接',exact:true}).click();
  await expect.poll(() => sockets).toEqual(['ssh']);
  await expect(serverTab(page,'Linux Server QA')).toBeVisible();
  expect(errors).toEqual([]);
});

test('renaming an SSH connection updates its existing tab names and preserves session numbers', async ({page}) => {
  const {sockets,errors} = await setup(page);
  await page.getByRole('treeitem',{name:'打开连接 Linux Server QA',exact:true}).dblclick();
  await expect.poll(() => sockets.length).toBe(1);
  await page.locator('.toolbar').getByRole('button',{name:'连接设置',exact:true}).click();
  const dialog = page.getByRole('dialog',{name:'编辑服务器连接'});
  await dialog.getByLabel('连接名称',{exact:true}).fill('Renamed Linux QA');
  await dialog.getByRole('button',{name:'保存并连接',exact:true}).click();
  await expect(dialog).not.toBeVisible();
  await expect.poll(() => sockets.length).toBe(2);
  await expect(serverTab(page,'Renamed Linux QA')).toBeVisible();
  await expect(serverTab(page,'Renamed Linux QA (2)')).toHaveAttribute('aria-selected','true');
  expect(errors).toEqual([]);
});

test('Kubernetes retains its workspace hierarchy', async ({page}) => {
  await setup(page);
  // Selecting Kubernetes opens its workspace. Stub its inventory independently
  // so this check concerns navigation rather than an external cluster.
  await page.route('**/api/connections/k8s-nav/k8s/**',route=>route.fulfill({json:{items:[]}}));
  await page.getByRole('treeitem',{name:'打开连接 Cluster QA',exact:true}).click();
  await expect(page.locator('.remote-category-node')).toContainText('Kubernetes 工作区');
  await expect(page.locator('.remote-session-node')).toContainText('K8s 工作区 1');
});

test('a delayed database switch cannot load SQL metadata through the active RDP server', async ({page}) => {
  const {errors} = await setup(page);
  const desktop = page.getByRole('treeitem',{name:'打开连接 Windows Server QA',exact:true});
  await desktop.dblclick();
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  let requested = false;
  const invalid: string[] = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname.includes('/rdp-nav/databases/')) invalid.push(request.url());
  });
  await page.route('**/api/connections/mysql-nav/databases', async route => {
    requested = true; await pending;
    await route.fulfill({json:[{name:'app',charset:'utf8mb4',collation:''}]});
  });
  await page.getByRole('treeitem',{name:'打开连接 Database QA',exact:true}).click();
  await expect.poll(() => requested).toBe(true);
  await desktop.dblclick();
  await expect(serverTab(page,'Windows Server QA (2)')).toHaveAttribute('aria-selected','true');
  release();
  await expect(page.locator('.rdp-session:visible .remote-dot.connected')).toBeVisible();
  await expect.poll(() => page.getByRole('tab',{selected:true}).innerText()).toContain('Windows Server QA');
  expect(invalid).toEqual([]);
  expect(errors).toEqual([]);
});
