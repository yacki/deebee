# DeeBee

DeeBee 是一个面向 MySQL 和 PostgreSQL 的浏览器数据库工作台。Python/FastAPI 后端按连接分发数据库驱动，Vue 3 工作台提供对象树、SQL 元数据智能补全、多标签查询、结果网格、事务、原地数据编辑、导入导出向导和可视化表结构设计。PostgreSQL 会按真实层级展示 `数据库 → Schema → 对象`，不同 Schema 的对象、补全和数据标签相互隔离。

完整功能及测试映射见 `docs/FEATURE_MATRIX.md`。

## 本地启动

1. 复制 `backend/.env.example` 为 `backend/.env`，填写工作台登录信息；其中的数据库参数会在首次启动时生成初始连接。
2. 在 `backend` 中创建 Python 3.12 虚拟环境并安装 `requirements.txt`。
3. 运行 `uvicorn deebee.main:app --host 127.0.0.1 --port 8000`。
4. 在项目根目录运行 `npm install && npm run dev`。
5. 打开 `http://localhost:5173`。

## 数据库连接

登录后点击左侧“连接”标题旁的 `+`，即可新建 MySQL 或 PostgreSQL 服务器连接。连接窗口支持保存前测试、编辑、复制和删除；有多个服务器时可在顶部选择器中切换。PostgreSQL 的默认数据库和默认 Schema 是两个独立字段，侧边栏按 `数据库 → Schema → 对象` 展示。

连接保存在 `backend/data/connections.json`，密码使用 `DEEBEE_TOKEN_SECRET` 派生的密钥加密，文件权限会设置为仅当前用户可读写。请固定使用一个足够长的随机 `DEEBEE_TOKEN_SECRET`；修改它后，已经保存的密码将无法解密。可用 `DEEBEE_CONNECTIONS_FILE` 改写保存路径。

`.env` 中的数据库参数只在连接文件尚不存在时用于初始化。MySQL 默认启用；启用 PostgreSQL 初始连接时设置：

```dotenv
DEEBEE_POSTGRES_ENABLED=true
DEEBEE_POSTGRES_HOST=127.0.0.1
DEEBEE_POSTGRES_PORT=5432
DEEBEE_POSTGRES_USER=postgres
DEEBEE_POSTGRES_PASSWORD=change-me
DEEBEE_POSTGRES_DATABASE=postgres
DEEBEE_POSTGRES_SCHEMA=public
```

如只使用 PostgreSQL，可同时设置 `DEEBEE_MYSQL_ENABLED=false`。这两个 `*_ENABLED` 开关仅控制首次初始化哪些 `.env` 连接，不限制之后在界面中新建任一类型的连接。

也可以配置好 `backend/.env` 后运行 `docker compose up --build`。Compose 使用命名卷持久化已保存的服务器连接。

## 测试

后端集成测试会连接 `.env` 中的 MySQL，并只操作 `deebee_e2e` 中的测试对象：

```bash
cd backend
.venv/bin/python -m pytest -q
```

前端部署构建：

```bash
npm run build
```

浏览器端 E2E（需要前端运行在 5173、后端运行在 8000）：

```bash
npx playwright install chromium
npm run test:e2e
```

全部测试：

```bash
npm run test:all
```
