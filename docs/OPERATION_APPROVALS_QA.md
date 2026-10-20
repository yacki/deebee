# 操作审批验收记录

日期：2026-10-20（Australia/Perth）。使用当前源码运行独立的回环实例 `http://127.0.0.1:18080/deebee`，数据保存在忽略提交的 `data/approval-qa/`。

普通系统账号为 `approval_tester`，显示名“危险操作审批体验账号”。登录密码由随机数生成，保存在本机权限为 0600 的 `data/approval-qa/credentials.json`；该文件还包含验收实例的管理员登录信息和资源 ID，不纳入版本控制。

远端测试使用新建数据库 `deebee_approval_1020_eb67fc` 和 SSH 目录 `/tmp/deebee_approval_1020_eb67fc`。MySQL 新建只读、写入两个测试账号，仅授予该测试数据库的权限；SSH 使用已核验的无 sudo 受限账号，本次命令只操作新建测试目录。

| 验收项 | 实际结果 |
| --- | --- |
| 普通人员提交 DELETE | 审批前 `items.id=1` 仍存在，通过后该行删除，`id=2` 保留 |
| 拒绝 DROP TABLE | `reject_target` 仍存在，原有两行数据保留 |
| 通过 DROP TABLE | `drop_target` 删除，数据库结构接口不再返回该表 |
| 通过 SSH 删除 | 新建 `approved` 子目录删除，退出码为 0 |
| 拒绝 SSH 删除 | 新建 `rejected` 子目录保留 |
| SSH 绕过边界 | 普通人员请求原始交互终端返回 403 / `APPROVAL_COMMAND_MODE_REQUIRED` |
| 自定义规则 | 为指定数据库配置 `SELECT 42` 文本规则，命中后进入审批；停用后直接执行并返回 42 |
| 管理界面 | 规则新增、编辑、停用、删除及模板新增均通过真实浏览器操作 |
| 远端独立核验 | 直接读取 MySQL 和 SSH：`items` 仅保留 `id=2`，拒绝目标保留，获批目标删除 |
| 管理员体验申请 | 留下 `DROP TABLE pending_target` 和删除 SSH `pending` 子目录两条待审批申请；默认 30 分钟有效，未执行 |

最后一项的申请 ID、原始命令和到期时间保存在 `data/approval-qa/pending-examples.json`。到期后可用普通账号重新提交，或按文档重新准备验收数据。服务重启会使待审批申请失效。

验证结果：

- 后端：256 项通过，38 项依赖其他可选环境的测试跳过。
- 新增审批真实浏览器验收：4 项通过。
- SSH 主机指纹、RDP 与录像界面回归：24 项通过，1 项依赖真实 K8s 环境的用例跳过。
- 前端单元测试：10 项通过。
- Vue/TypeScript 生产构建、ESLint、`git diff --check` 通过。

后端覆盖审批前不触发执行、通过与拒绝、竞争决策、重复审批、幂等、取消、超时、授权及凭据撤销、规则变更、SQL 注释和字符串伪装、Shell 命令变形、MCP 统一拦截、API 越权、重启不重放、审计失败不派发、模板持久化和资源范围限制。

界面截图：

- [待审批列表](../test-results/approval-pending-queue.png)
- [完整审核详情](../test-results/approval-pending-detail.png)
- [规则与模板](../test-results/approval-rules.png)

实现与配置说明见 [操作审批说明](OPERATION_APPROVALS.md)。
