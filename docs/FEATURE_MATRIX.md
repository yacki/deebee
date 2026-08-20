# DeeBee MySQL 功能与 E2E 矩阵

本矩阵是 P0、P1、P2 的验收基线。浏览器用例位于 `tests/e2e/workbench.spec.ts`，真实 MySQL API 用例位于 `backend/tests`。

| 领域 | 已实现功能 | E2E 覆盖 |
|---|---|---|
| Vue 与图标 | Vue 3 单页工作台；Iconify Lucide 图标本地打包，不依赖运行时公网请求 | 生产构建断言；浏览器树与工具栏图标验收 |
| 对象树 | 26px 独立展开热区；单击选择与展开互不干扰；深层对象固定网格对齐；大量表滚动 | 浏览器连续收起/展开和 13+ 表图标坐标断言 |
| 数据库右键 | 打开/关闭、属性、新建、删除、新建查询、控制台、SQL 文件、转储、打印、搜索、刷新 | 浏览器菜单用例；`test_create_and_delete_database_with_confirmation_api` |
| 表右键 | 打开、设计、新建、删除、清空、TRUNCATE、复制结构/数据、权限、导入、导出、数据生成、转储、维护、复制名称/DDL、重命名、刷新 | 浏览器菜单用例；`test_database_object_and_completion_workflows` |
| 其他对象 | 视图、函数、过程、触发器、事件浏览；打开定义、编辑 SQL、删除；事件启用/禁用 | `test_database_object_and_completion_workflows`；对象树浏览器验收 |
| 对象列表 | 表/视图/例程/触发器/事件列表，筛选，表统计、大小、引擎、时间、排序规则、注释 | 浏览器对象列表验收 |
| SQL 编辑器 | MySQL 高亮、格式化、片段、当前数据库的表/视图/字段/函数/关键字补全、表别名字段解析、当前语句/选区执行 | 浏览器真实表名与 `alias.` 字段提示用例；catalog API 测试 |
| 查询数据库 | 标签级数据库选择；切换时创建目标库会话并关闭旧会话；目录和状态栏同步 | 浏览器在 `mysql` 与 `deebee_e2e` 间执行 `SELECT DATABASE()` |
| 查询标签 | 多标签、复制、关闭其他/右侧、本地保存与重新打开查询、历史、自动提交、提交、回滚、取消查询 | 浏览器多标签/保存/历史/事务用例；`test_query_cancellation`、`test_workspace_session_cleanup` |
| 查询结果 | Message/Summary/Result/Profile/Status，多结果集、行/单元格选择、Copy As 六种格式、持久列宽、行高、冻结列、跳转记录 | 浏览器结果网格菜单及状态用例；`test_complete_mysql_workflow` |
| 数据浏览 | 服务端分页、排序、筛选，新增、双击单元格原地编辑、删除、NULL/JSON 展示、可拖动且持久化列宽，CSV/JSON/SQL/XLSX 导出 | 浏览器原地编辑持久化/列宽/筛选用例；`test_complete_mysql_workflow` |
| 表设计器 | 字段、主键、索引、外键、CHECK、生成列、默认值、表引擎、字符集、排序规则、表注释、DDL 预览和危险操作确认 | 浏览器设计器用例；`test_table_designer_create_and_alter`；生成列回归 |
| 数据向导 | CSV/JSON/XLSX 导入，CSV/JSON/SQL/XLSX 导出，SQL 文件执行，结构/数据转储，测试数据生成 | 浏览器向导用例；`test_wizards_dump_script_and_generated_data_roundtrip` |
| 后台任务 | 导入、SQL 文件、数据生成的进度轮询、失败信息和取消 | `test_background_jobs_report_progress_results_and_cancellation` |
| 维护与权限 | CHECK、ANALYZE、OPTIMIZE、REPAIR；当前账户授权查看 | `test_database_object_and_completion_workflows` |
| 安全 | 标识符引用、DDL 预览一致性、危险操作确认、登录令牌、会话清理、密码不返回前端 | 后端完整工作流及 DDL E2E |
| 布局 | 1280 和 1024 宽度、水平大表、固定对象树、菜单贴边、向导及结果区布局 | 集成浏览器视觉验收 |

不属于 P0/P1/P2 的模型图、图表、自动化调度和打印模板仍作为后续独立模块，不在本矩阵中伪装为已完成。
