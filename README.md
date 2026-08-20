# DeeBee

DeeBee 是一个面向 MySQL 的浏览器数据库工作台。Python/FastAPI 后端持有数据库连接，Vue 3 工作台使用本地打包的 Iconify 图标，提供 Navicat 风格的对象树、右键命令、SQL 元数据智能补全、多标签查询、专业结果网格、事务、原地数据编辑、导入导出向导和可视化表结构设计。

完整功能及测试映射见 `docs/FEATURE_MATRIX.md`。

## 本地启动

1. 复制 `backend/.env.example` 为 `backend/.env`，填写 MySQL 和登录信息。
2. 在 `backend` 中创建 Python 3.12 虚拟环境并安装 `requirements.txt`。
3. 运行 `uvicorn deebee.main:app --host 127.0.0.1 --port 8000`。
4. 在项目根目录运行 `npm install && npm run dev`。
5. 打开 `http://localhost:5173`。

也可以配置好 `backend/.env` 后运行 `docker compose up --build`。

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
