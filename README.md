# DeeBee

DeeBee 是一个支持 MySQL 和 PostgreSQL 的浏览器数据库工作台。它提供服务器连接管理、数据库与 Schema 对象树、SQL 编辑与元数据补全、结果编辑、事务、导入导出和表结构设计。

完整功能及测试映射见 `docs/FEATURE_MATRIX.md`。

## Docker 一键部署

只需要 Docker 与 Docker Compose：

```bash
git clone https://github.com/yacki/deebee.git
cd deebee
docker compose up -d
```

打开 `http://localhost:3000`，首次登录使用 `admin / deebee`。默认只监听本机地址；正式使用前请复制 `.env.example` 为 `.env` 并修改管理员密码。

登录后点击左侧“连接”标题旁的 `+`，在界面中添加 MySQL 或 PostgreSQL。数据库运行在 Docker 宿主机上时，连接主机名使用 `host.docker.internal`。

### 数据持久化

连接配置和加密密钥都保存在宿主机的 `./data` 目录：

- `data/connections.json`：已保存的服务器连接，密码为密文；
- `data/secret.key`：自动生成的本地加密密钥。

重启或重建容器不会丢失配置。迁移或备份时必须一起保留整个 `data` 目录；丢失 `secret.key` 后，已保存的数据库密码将无法解密。可以在 `.env` 中通过 `DEEBEE_DATA_DIR` 改为其他宿主机路径。

停止与再次启动：

```bash
docker compose down
docker compose up -d
```

更新版本：

```bash
git pull
docker compose up -d --pull always
```

## 预构建镜像

GitHub Actions 会为 `linux/amd64` 和 `linux/arm64` 发布以下 GHCR 镜像：

- `ghcr.io/yacki/deebee-api:latest`
- `ghcr.io/yacki/deebee-web:latest`

Compose 会直接拉取公开镜像，不需要 Docker Hub 账号；需要从源码构建时可执行 `docker compose up -d --build`。

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
