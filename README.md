# DeeBee

DeeBee 是一个支持 MySQL 和 PostgreSQL 的浏览器数据库工作台。它提供服务器连接管理、数据库与 Schema 对象树、SQL 编辑与元数据补全、结果编辑、事务、导入导出和表结构设计。

完整功能及测试映射见 `docs/FEATURE_MATRIX.md`。

## 直接运行

无需下载源码或本地打包，只要安装了 Docker，执行：

```bash
docker run -d --name deebee --restart unless-stopped \
  -p 127.0.0.1:3000:3000 \
  --add-host host.docker.internal:host-gateway \
  -v "$PWD/deebee-data:/app/data" \
  ghcr.io/yacki/deebee:latest
```

Docker 会自动从 GitHub Container Registry 拉取包含前端和后端的完整镜像。打开 `http://localhost:3000`，首次登录使用 `admin / deebee`。

默认只监听当前电脑。正式使用建议在命令中增加 `-e DEEBEE_ADMIN_PASSWORD='你的强密码'`；如果需要让局域网其他设备访问，将端口参数改为 `-p 3000:3000`，并务必设置强密码。

登录后点击左侧“连接”标题旁的 `+`，在界面中添加 MySQL 或 PostgreSQL。数据库运行在 Docker 宿主机上时，连接主机名使用 `host.docker.internal`。

### 数据持久化

连接配置和加密密钥都保存在运行命令所在目录的 `./deebee-data` 中：

- `deebee-data/connections.json`：已保存的服务器连接，密码为密文；
- `deebee-data/secret.key`：自动生成的本地加密密钥。

重启或重建容器不会丢失配置。迁移或备份时必须一起保留整个 `deebee-data` 目录；丢失 `secret.key` 后，已保存的数据库密码将无法解密。也可以把命令中冒号左边的 `$PWD/deebee-data` 换成其他宿主机绝对路径。

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

拉取公开镜像不需要 GitHub 或 Docker Hub 账号。开发者克隆源码后也可以执行 `docker compose up -d --build` 在本地构建同一个单容器版本；Compose 默认把配置写入仓库的 `./data` 目录。

## 本地开发

1. 复制 `backend/.env.example` 为 `backend/.env`。
2. 在 `backend` 中创建 Python 3.12 虚拟环境并安装 `requirements.txt`。
3. 运行 `uvicorn deebee.main:app --host 127.0.0.1 --port 8000`。
4. 在项目根目录运行 `npm install && npm run dev`。
5. 打开 `http://localhost:5173`。

后端测试：

```bash
cd backend
.venv/bin/python -m pytest -q
```

前端构建：

```bash
npm run build
```
