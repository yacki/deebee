# DeeBee

DeeBee 是一个浏览器一站式连接工作台。MySQL 和 PostgreSQL 已支持对象树、SQL 编辑与元数据补全、结果编辑、事务、导入导出和表结构设计；Redis、ClickHouse 和 MongoDB 当前支持连接配置、加密保存与真实连通性测试；SSH 提供交互终端，Remote Desktop 通过 Apache Guacamole 提供浏览器内 RDP 桌面。

完整功能及测试映射见 `docs/FEATURE_MATRIX.md`。

## 管理后台 / Agent 接入

管理入口为 `/deebee/admin`（工作台管理员），系统账号入口为 `/deebee/#access-user`。根路径部署对应 `/admin` 和 `/#access-user`。外部身份绑定系统账号，系统账号通过资源授权使用数据库或服务器中的资源账号。支持 API Key、企业登录（OIDC）、`/deebee/mcp/` 和等价 REST 服务；原有管理登录、协议字段与已保存授权保持兼容。

左侧菜单统一为：**外部身份、系统账号、资源管理、资源授权、执行记录、审计日志、系统设置**。

- **外部身份**：管理外部调用方到系统账号的绑定；API Key 在该页面的“访问凭证”页签管理，密钥原文只显示一次。
- **系统账号**：查看外部身份、访问凭证以及各个资源账号的授权关系。
- **资源管理**：每个资源直接展示资源账号，进入“资源账号”可添加、检查连接与权限、编辑或停用。支持环境、多项目组和标签分类，以及组合筛选、卡片/列表切换和分页；分类不增加新的权限层级。没有独立的“目标账号”菜单。
- **资源授权**：选择系统账号、资源和资源账号；通过中文操作选项、日期时间及数字限制配置权限，不需要填写 JSON 或 Unix 时间。“查看可用权限”收在同一页面。
- **系统设置**：集中配置身份验证服务、程序接入地址和备份；验证服务不是额外一套资源授权。

已有普通/高权限双账号授权均保留；可以只授权一个高权限资源账号，但调用仍必须显式指定 `mode: privileged`，并满足凭证与授权的 `privilege:use` 限制。默认调用不会自动提升权限。每个系统账号与资源之间，当前支持每种权限类别各一个资源账号。

管理控制台采用 Arco Design Vue，正文 14px、辅助文字 12px，侧栏与内容区独立滚动。JSON 查看和编辑支持格式化、压缩和只读原文；不完整 JSON 保留原文并提示，不尝试恢复截断内容。审计请求最多 1 KiB、响应最多 4 KiB，先脱敏再按 UTF-8 字节截断；历史截断内容不会恢复。

删除资源需要输入完整名称确认，并使用 `DELETE /api/admin/v1/resources/{id}`（`expected_version`、`confirm_name`）。关联资源账号和授权立即停用并从管理列表隐藏，后续访问被拒绝；正在执行的操作沿用现有撤权取消机制，不保证撤回已经发生的远端副作用。删除不触碰工作台连接和远端数据，保留内部删除标记及历史审计；被删除连接不会被自动同步或旧配置导入重新发布。分类修改保留已有账号核验，地址等连接配置修改仍需重新核验。

部署、无秘密 mapping 示例及独立 MCP 验收脚本见 [身份与访问说明](docs/ACCESS_V1.md)，实际测试及边界见 [实施验收记录](docs/ACCESS_IMPLEMENTATION_LOG.md)。本分支功能尚未推送到下方 `latest` 镜像；使用时需构建当前分支，不要直接替换已有实例。

### 前台连接自动入池

管理员保存的所有连接自动出现在“资源管理”；升级启动时会补齐已有连接，读取资源列表和后台每 30 秒也会核对一次。以原连接 ID 建立稳定引用，密码/私钥仍只保存在原连接配置中，不复制到访问控制库。

- 创建、修改、删除连接会同步资源池。删除保留停用记录及审计；地址、账号、密码等变化会停用关联账号并清除旧核验，重新核验后才能对外执行。仅改名保留已有核验。
- 全部连接都能入池。MCP 当前执行端支持 SSH/MySQL/PostgreSQL；其他类型、隧道/代理、缺少默认数据库或 SSH 主机指纹的连接会显示具体原因，不能误当作可执行资源。当前数据库资源仍限定一个默认数据库，不自动扫描整台数据库服务器。
- 出现在资源管理中不等于允许访问：原管理员账号不会自动发布给 Agent，不会创建新的 API Key 或资源授权。仍需启用资源、核验资源账号权限并授权给系统账号。
- 若连接保存后同步暂时失败，前台提示“同步待重试”，无需重复创建连接；后台及下一次读取资源池会重试。原连接变化或删除会立即使旧凭据引用失效。

## 直接运行

无需本地打包，只要安装了 Docker，先创建内部网络并启动 RDP 网关：

```bash
docker network create deebee-net
docker run -d --name deebee-guacd --restart unless-stopped \
  --network deebee-net \
  guacamole/guacd:1.6.0
```

再启动 DeeBee：

```bash
docker run -d --name deebee --restart unless-stopped \
  --network deebee-net \
  -p 127.0.0.1:3000:3000 \
  --add-host host.docker.internal:host-gateway \
  -e DEEBEE_GUACD_HOST=deebee-guacd \
  -v "$PWD/deebee-data:/app/data" \
  ghcr.io/yacki/deebee:latest
```

Docker 会自动从 GitHub Container Registry 拉取包含前端和后端的完整镜像。打开 `http://localhost:3000/deebee/`，首次登录使用 `admin / deebee`。访问根路径时也会自动跳转到 `/deebee/`。

默认只监听当前电脑。正式使用建议在命令中增加 `-e DEEBEE_ADMIN_PASSWORD='你的强密码'`；如果需要让局域网其他设备访问，将端口参数改为 `-p 3000:3000`，并务必设置强密码。

登录后点击左侧“连接”标题旁的 `+`，可添加 MySQL、PostgreSQL、Redis、ClickHouse、MongoDB、SSH 或 Remote Desktop。目标服务运行在 Docker 宿主机上时，连接主机名使用 `host.docker.internal`。SSH 支持密码和加密私钥认证，并在首次连接时确认主机密钥指纹；RDP 支持域、NLA/TLS/RDP 安全模式、动态分辨率和全屏。

### 路径与反向代理

默认访问前缀是 `/deebee`。可以通过 `-e DEEBEE_BASE_PATH=/其他路径` 修改；设为 `/` 则部署在域名根路径。前端资源和 API 会自动跟随这个路径，无需重新构建镜像。

Nginx 反向代理到本机 3000 端口时，应保留 `/deebee` 前缀。注意 `proxy_pass` 后面不能带 `/`：

```nginx
location = /deebee {
    return 301 /deebee/;
}

location ^~ /deebee/ {
    proxy_pass http://127.0.0.1:3000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    client_max_body_size 60m;
    proxy_read_timeout 3600s;
}
```

### 数据持久化

连接配置和加密密钥都保存在运行命令所在目录的 `./deebee-data` 中：

- `deebee-data/connections.json`：已保存的服务器连接，密码和 SSH 私钥为密文；
- `deebee-data/secret.key`：自动生成的本地加密密钥。

重启或重建容器不会丢失配置。迁移或备份时必须一起保留整个 `deebee-data` 目录；丢失 `secret.key` 后，已保存的连接密码和 SSH 私钥将无法解密。也可以把命令中冒号左边的 `$PWD/deebee-data` 换成其他宿主机绝对路径。

停止与再次启动：

```bash
docker stop deebee
docker start deebee
```

更新版本：

```bash
docker pull ghcr.io/yacki/deebee:latest
docker rm -f deebee
# 再次执行上面的 docker run 命令；deebee-data 中的配置会继续保留
```

## 预构建镜像

GitHub Actions 会为 `linux/amd64` 和 `linux/arm64` 发布一个前后端合并的公开镜像：

- `ghcr.io/yacki/deebee:latest`

拉取公开镜像不需要 GitHub 或 Docker Hub 账号。开发者克隆源码后也可以执行 `docker compose up -d --build`；Compose 会同时启动 DeeBee 和仅在内部网络可见的 guacd，配置默认写入仓库的 `./data` 目录。

## 本地开发

1. 复制 `backend/.env.example` 为 `backend/.env`。
2. 在 `backend` 中创建 Python 3.12 虚拟环境并安装 `requirements.txt`。
3. 前后端分开开发时，在 `backend` 中运行 `DEEBEE_BASE_PATH=/ uvicorn deebee.main:app --host 127.0.0.1 --port 8000`。
4. RDP 联调时运行 `docker run --rm -d --name deebee-guacd -p 127.0.0.1:4822:4822 guacamole/guacd:1.6.0`。
5. 在项目根目录运行 `npm install && npm run dev`。
6. 打开 `http://localhost:5173`。

后端测试：

```bash
cd backend
.venv/bin/python -m pytest -q
```

前端构建：

```bash
npm run build
```
