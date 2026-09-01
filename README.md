# DeeBee

DeeBee 是一个浏览器一站式连接工作台。MySQL 和 PostgreSQL 已支持对象树、SQL 编辑与元数据补全、结果编辑、事务、导入导出和表结构设计；Redis、ClickHouse 和 MongoDB 当前支持连接配置、加密保存与真实连通性测试；SSH 提供交互终端，Remote Desktop 通过 Apache Guacamole 提供浏览器内 RDP 桌面。

完整功能及测试映射见 `docs/FEATURE_MATRIX.md`。

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
