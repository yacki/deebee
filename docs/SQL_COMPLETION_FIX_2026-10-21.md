# SQL 智能补全修复与验证（2026-10-21）

`SELECT * FROM core_app whe;` 以前仍被识别为表名输入位置，只返回表名；Monaco 的模糊匹配又将 `dev_webhook` 匹配到 `whe`，导致 WHERE 缺失、无关对象抢占候选。现在该位置只显示 WHERE，按 Enter/Tab 后保留原有分号和后续 SQL。

代码保留在本地，未提交、未推送；已于 2026-10-22 00:09（Australia/Perth）按用户要求部署前端。没有改动数据库、连接配置、用户查询草稿或其他现有数据。全部浏览器测试使用独立浏览器存储及模拟 API，不连接真实后端；补全测试还会拦截并断言没有 SQL 执行、数据写入请求。

## 行为与界面

- 区分表名、子句、字段表达式，使用前缀匹配；不再将全库字段、任意函数和文档单词混入候选。
- 按当前语句及子查询解析表和别名，并读取光标后的 FROM；同名 JOIN 字段使用别名前缀。
- 空 WHERE 优先显示当前表字段；函数按已输入前缀提供，存储过程不作为表达式函数出现。
- 按 MySQL、PostgreSQL、SQL Server 区分函数和关键词；保留反引号、双引号和方括号。
- 在字符串和注释内停止补全；从单词中间接受补全时替换完整单词，保留后续内容。
- 候选行显示简短中文说明，去除英文操作栏和重复详情面板。长名称省略显示，右侧类型及列数保留；候选框在窗口边缘自动调整位置。

## 20 个浏览器用例

测试源码：`tests/e2e/sql-completion.spec.ts`。最终生产构建下 20/20 通过。

| Case | 验证内容 |
| --- | --- |
| C01 | 真实逐字输入 whe 自动弹出 WHERE；首项选中、Enter 插入、分号保留 |
| C02 | 完整表名及别名后只给相关子句，无全库字段、函数及 DDL 噪声 |
| C03 | FROM 表名前缀严格匹配；表、视图的简短说明与 Tab 插入 |
| C04 | JOIN 表名补全及表别名后的 ON |
| C05 | WHERE 只给当前表字段；空 WHERE 字段优先，无无效逻辑词或函数堆积 |
| C06 | alias-dot 只给该别名字段，插入时不重复别名前缀 |
| C07 | JOIN 同名字段分别显示 a.id/u.id，裸字段前缀也能选择 |
| C08 | SELECT 列表逗号属于表达式；从光标后 FROM 获取字段 |
| C09 | FROM 列表逗号后补全下一张表 |
| C10 | 从 whe\|re 中间接受补全，保留后续条件和分号 |
| C11 | 多语句作用域隔离；字符串内分号不切断语句 |
| C12 | 嵌套、相邻子查询及外层别名不串字段 |
| C13 | GROUP BY、ORDER BY 的 BY 及排序/分组字段补全 |
| C14 | UPDATE → SET → 当前表字段；INSERT INTO → VALUES |
| C15 | 单引号、行注释、块注释、美元引号内抑制候选 |
| C16 | COUNT 函数片段可插入参数；自定义函数与存储过程区分 |
| C17 | PostgreSQL ILIKE/JSONB 函数及双引号字段；排除 MySQL IFNULL |
| C18 | SQL Server TOP/GETDATE 及方括号字段；排除 MySQL IFNULL |
| C19 | 数据库限定名、反引号、未知别名、未知命名空间和多查询标签隔离 |
| C20 | 1440×900、1024×768、800×500 窗口边界；行高、长名称及说明、方向键选中和 Escape |

## 其他验证

- `npm test`：生产构建成功；34/34 单元测试通过，其中新增 SQL 分析测试 24 个，覆盖作用域、引用范围、引号及结果可编辑性回归。
- 生产构建下原有 `ui-workbench.spec.ts`：5/5 通过，覆盖标签、表单、结果编辑、设计器及文件选择；请求均由模拟 API 处理。
- 修改涉及的 TypeScript/Vue 和浏览器测试 ESLint 通过；`git diff --check` 通过。

复测命令（先在另一终端启动 `npx vite preview --host 127.0.0.1 --port 4173`）：

```sh
npm test
DEEBEE_WEB_URL=http://127.0.0.1:4173 npx playwright test tests/e2e/sql-completion.spec.ts tests/e2e/ui-workbench.spec.ts --workers=1
```

界面截图：

- [WHERE 原场景修复](qa/2026-10-21/sql-completion/where-fixed.png)
- [1440×900](qa/2026-10-21/sql-completion/popup-1440.png)
- [1024×768](qa/2026-10-21/sql-completion/popup-1024.png)
- [800×500，长名称与说明](qa/2026-10-21/sql-completion/popup-800.png)

## 2026-10-22 发布验证

已发布到 https://deebee.idmesh.site/deebee/ ，刷新页面生效。本次只热更新前端，后端容器 ID、启动时间、Python 源码以及 guacd、网关容器均保持不变。配置、连接文件、密钥和信任文件哈希一致；未对业务数据库执行查询或写入。

- 66 个容器内发布文件校验通过，65 个公网文件 SHA256 与已测试构建一致。
- 线上页面重新运行 20 个补全浏览器用例，20/20 通过；使用真实线上静态资源和模拟 API，不连接真实数据库。
- 121 个旧哈希资源校验保持一致，已打开页面仍可加载旧资源。
- 健康检查正常；Compose 已指向包含同版前端的新镜像，下次容器重建仍使用此版本。没有重启现有后端或远程会话服务。
- 发布前备份位于 `/data/deebee/releases/sql-completion-20261022-000519/backup/`，包含旧前端、Compose、环境、数据目录副本及完整性检查通过的 SQLite 在线备份。
- 没有创建 Git 提交，没有推送代码或镜像。

[发布校验记录](qa/2026-10-22/sql-completion/deployment.json)，[线上 WHERE 截图](qa/2026-10-22/sql-completion/where-online.png)，[线上窄窗口截图](qa/2026-10-22/sql-completion/popup-800-online.png)。
