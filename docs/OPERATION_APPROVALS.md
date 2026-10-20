# 数据库与 SSH 操作审批

管理入口：`/deebee/admin` → **操作审批**。普通人员入口：`/deebee/#access-user`。根路径部署时省略 `/deebee`。

审批适用于普通系统账号和 Agent 通过人员入口、REST 或 MCP 发起的 MySQL、PostgreSQL 和 SSH 操作。普通系统账号可以申请使用已经授予的数据库写入账号，但不会获得后台管理员身份。审核只能使用原有本地管理员登录。RDP 和 Kubernetes 保持原有行为。

## 操作流程

1. 管理员照常登记、核验资源账号，并授予普通系统账号或用户组相应的操作权限。
2. 用户提交 SQL 或 SSH 命令，可填写申请原因。命中规则时返回 `pending_approval`，此时不创建目标连接，不执行原始请求。
3. 管理员在“审批申请”中按状态、资源、申请人筛选，打开详情，核对申请人、目标、资源账号、完整命令或 SQL、参数及 sudo 提权方式。
4. 填写审核意见，选择“通过并执行”或“拒绝申请”。通过后，系统使用申请人原有授权重新核验并执行原始请求；拒绝不会执行。
5. 用户页面自动刷新当前申请，也可用“刷新我的申请”找回最近 100 条记录。管理员可在审批详情和执行记录中查看最终结果。

审批不增加凭据或资源权限。数据库写操作仍要求 `db:write`、显式 `mode: privileged` 及 `privilege:use`；真正只读的数据库资源账号不能因审批变成可写账号。

## 默认危险操作模板

首次升级自动启用七条模板。管理员可编辑、停用、删除，或通过“危险命令模板”重新新增；重启不会恢复被删除或停用的模板。

| 模板 | 检测内容 |
| --- | --- |
| 数据库数据变更 | INSERT、UPDATE、DELETE 等数据变更 |
| 数据库结构变更 | CREATE、ALTER、DROP、TRUNCATE 等结构变更 |
| 无 WHERE 的更新和删除 | 使用 SQL 语法树判断，注释或字符串中的 WHERE 不算过滤条件 |
| 文件删除和磁盘写入 | rm、rmdir、shred、dd、mkfs、wipefs、truncate |
| 提权和权限修改 | sudo、su、chmod、chown、用户与权限管理，包括显式 `elevation: sudo` |
| 系统和服务变更 | 重启关机、服务管理、进程终止、防火墙和软件包管理 |
| 其他 Shell 命令（保守兜底） | 除少量固定只读命令外，其他任意 Shell、脚本、解释器、复合命令、重定向及无法判断的输入都需审批 |

默认可以直接执行 `id`、`whoami`、`pwd`、`uptime`、`ls` 以及实现中限定参数的 `id`、`uname`、`df`、`free`；`ssh.inspect` 的固定只读诊断保持直接执行。保守兜底会让部分无害但无法可靠判断的命令也进入审批。

自定义规则支持内置检测、包含文本、文本前缀和全部操作。文本忽略大小写并合并空白；SQL 同时匹配去注释的规范化语句。规则可限定同协议的多个资源，留空表示该协议全部资源。多条规则按“任意命中即审批”处理，有效期取命中规则的最短值，并受申请人凭据、用户组成员关系和授权到期时间限制。默认 30 分钟，可配置 1～1440 分钟。

文本规则适合补充业务要求。禁用 SSH 保守兜底后，单独依靠文本匹配不能防止解释器、编码或命令变形绕过审核。

## SSH 交互终端边界

启用任意适用 SSH 审批规则后，普通系统账号使用逐条命令执行，不开放原始交互终端。已有交互会话会在授权检查时断开，新会话的 REST 和 WebSocket 路径也会拒绝。这样可以避免通过终端编辑器、历史命令、粘贴控制符或嵌套 Shell 绕过审批。管理员原工作台的 SSH 终端保留原有行为。

如需恢复某个资源的普通账号交互终端，管理员必须明确停用或调整适用于它的所有 SSH 审批规则，并保留原有 `ssh:connect` 授权。

## 有效性、幂等与恢复

- 审核绑定原始请求的 SHA-256、原有资源账号和完整授权路径。调用方不能携带“已审批”字段绕过检测，也不能把批准结果用于其他命令。
- 同一幂等键及相同参数始终返回同一个执行记录；改变参数返回冲突。审批通过、拒绝、取消或过期后不能用旧幂等键重放。
- 同一个申请只能处理一次。申请人取消、管理员拒绝、审批超时、凭据失效、授权变更或策略变化都不会自动执行。
- 通过前、实际执行前均重新核验申请人身份和授权。策略、资源、账号、用户组或授权在等待期间变化后，旧申请需重新提交。
- 原始请求和结果沿用加密存储及现有保留期限；审核人、意见、规则快照和状态进入执行记录和审计。审批规则包含在现有配置导出、预览和导入中。
- 待审批上下文中的登录凭据仅在进程内存里保留。服务停止或重启会明确将待审批申请标记为 `expired`，保留历史记录，原命令未执行，用户需重新提交。已经开始执行的请求沿用原有 `unknown` 恢复语义，不自动重放。
- 每个申请人最多 20 条待审批申请，全局最多 200 条；获批执行仍受原有并发、超时和输出限制。

## API

| 接口 | 用途 |
| --- | --- |
| `GET /api/admin/v1/approval-templates` | 获取内置危险操作模板 |
| `GET/POST /api/admin/v1/approval-rules` | 列出或新增审批规则 |
| `PATCH/DELETE /api/admin/v1/approval-rules/{id}` | 修改或软删除规则，需要 `expected_version` |
| `GET /api/admin/v1/approvals` | 筛选和分页获取审批申请；支持 `status`、`resource_id`、`owner`、`offset`、`limit` |
| `GET /api/admin/v1/executions/{id}` | 管理员读取完整待执行请求、请求哈希及结果 |
| `POST /api/admin/v1/approvals/{id}/decision` | 提交 `decision: approve/reject`、非空 `comment` 和已审核的 `request_hash` |
| `GET /api/v1/executions` | 获取当前申请人及身份绑定最近 100 条记录 |
| `GET /api/v1/executions/{id}` | 获取自己的审批状态和执行结果 |
| `POST /api/v1/executions/{id}/cancel` | 取消自己的待审批申请或请求取消执行 |

原有 REST 和 MCP 执行工具无需更换。调用方需要识别 `pending_approval`、`rejected`、`expired`，等待人工审批；不得自动更换幂等键重提被拒绝、失效或结果未知的操作。`ExecutionInput` 增加可选 `approval_reason` 字段。

## 验证

```sh
backend/.venv/bin/pytest -q backend/tests
npm run build
node --test tests/*.test.mjs
npm run lint
```

真实浏览器验收需显式设置私有配置文件，仅允许回环地址，测试会修改专用资源里的数据：

```sh
DEEBEE_APPROVAL_QA_CONFIG=/private/path/approval-qa.json \
  npx playwright test tests/e2e/approvals-live.spec.ts --workers=1
```

私有配置字段为 `base`、`db_resource`、`ssh_resource`、`database`、`remote_dir`、`username`、`password`、`admin_username`、`admin_password`。资源必须是新建的独立测试数据库和目录；数据库提前创建 `items`、`drop_target`、`reject_target`、`pending_target` 测试表，SSH 目录准备 `approved`、`rejected`、`pending` 子目录。请勿指向业务数据。
