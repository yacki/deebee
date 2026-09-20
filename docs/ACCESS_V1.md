# DeeBee 身份与访问 V1

实现分支：`codex/identity-access-v1`。新服务只支持 **SSH、MySQL、PostgreSQL**。原工作台、登录、连接管理、事务、RDP 和其他驱动保留原入口；外部身份不能使用这些兼容接口。

## 入口及部署

| 入口（相对部署前缀） | 用途 |
|---|---|
| `/` | 原工作台，不修改布局 |
| `/admin` | 身份与访问后台，仅原本地管理员；旧 `/#access` 仍兼容 |
| `/#access-user` | 映射用户本地密码 / OIDC 登录、资源与执行 |
| `/api/admin/v1` | 管理接口，仅原管理员 Token |
| `/api/v1` | 已授权身份的数据接口 |
| `/mcp/` | 远程 MCP Streamable HTTP，官方 Python SDK 1.30.0 |
| `/.well-known/oauth-protected-resource` | OAuth Protected Resource Metadata |
| `/openapi.json` | REST OpenAPI；管理配置字段另见代码模型和本文 |

例如部署前缀是 `/deebee`，管理入口为 `/deebee/admin`，MCP 为 `/deebee/mcp/`。部署在根路径时分别为 `/admin` 与 `/mcp/`。

安装更新后的 `backend/requirements-runtime.txt` 并执行 `npm run build`。使用现有部署方式构建分支镜像；不要把本地验收服务当生产发布。`DEEBEE_PUBLIC_URL` 应为固定 HTTPS URL，例如 `https://host/deebee`，包含前缀但无末尾斜线。反向代理须保留认证头，但代理日志不得记录请求体、认证头、OIDC 回调查询参数；应用内部另有脱敏审计。建议 Uvicorn 使用 `--no-access-log`。外部访问只开放所需数据接口，管理入口限制在可信管理网络。

V1 要求 **一个实例、一个 worker**；同一 access 目录的第二个 worker 会拒绝启动。SQLite 与旧连接文件仍在原进程内，但采用独立配置、凭据密钥和执行连接。数据库不可写时不提交新的远端任务。不要把多个副本指向同一个 SQLite 文件。

新配置为空时没有外部主体能执行资源；原连接不会自动发布。默认本地 Key 源只是签发命名空间，不会自动产生 Key 或授权。

## 身份及账号模型

```text
外部 API-Key → 固定可信验证服务 ─┐
OIDC Access Token → JWT/在线校验 ├→ (source_id, subject) → 显式绑定 → Principal
本地签发 API-Key → Key 摘要校验 ┘                                 │
本地用户名密码 → 独立映射用户会话 ────────────────────────────────┤
                                                              ↓
                          Principal × Resource → AccessGrant
                                      ├─ normal_account_id（默认）
                                      └─ privileged_account_id（需勾选）
                                                              ↓
                凭据 scopes ∩ 授权 actions ∩ 特权开关 ∩ 有效期/状态
                                                              ↓
                   后端读取凭据 → SSH / MySQL / PostgreSQL
                                                              ↓
                          执行 ID、结构化结果、审计（不返回凭据）
```

内部人员 `human` 可设置本地用户名和密码（至少 12 位、Argon2）；服务身份 `service` 不可设置人员密码。用户名不与外部 email/name 自动匹配。原管理员用户名保留，不能用外部身份覆盖。人员改密/管理员重置撤销该人员的旧本地会话，但不修改目标账号密码或原管理员登录。

一个来源中的 subject 只能绑定一个 Principal；同一个 Principal 可以绑定多个来源，但执行结果的读取与取消还绑定原来源绑定 ID，不能跨来源读取。一个 Principal 对同一 Resource 只有一份 Grant。

普通账号、特权账号是**目标系统中的真实账号**，不是 DeeBee 管理员角色。默认普通；特权必须同时满足：后台勾选、特权账号已核验并启用、凭据有 `privilege:use`、Grant actions 包含该 scope、请求显式 `mode: privileged`。数据库写入另外需要 `db:write`。不自动提权、不自动 sudo、不把勾选变成 root 权限；V1 没有逐次审批。

## 管理员配置顺序

1. 打开 `/admin`，沿用原本地管理员登录。
2. 新建内部身份；起始无任何资源权限。
3. 配置并测试身份源，填写显式身份绑定。
4. 新建直连资源：SSH 登记可信主机指纹及对应算法；数据库固定 database，PostgreSQL 固定 schema 范围，生产强制验证 TLS。
5. 保存**禁用**的普通/特权账号，填写后端凭据；点击“测试与核验”。检查检测结果和目标系统实际权限，填写核验说明再启用。账号不能移动到另一个资源。修改用户名、凭据、级别后需重新核验。
6. 配置资源授权，默认不勾特权。actions 和 limits 可以进一步限制能力。
7. “授权预览”查看投射结果，考虑实际 Key/Token scopes 的交集。
8. 签发本地 Key 或让外部身份源签发凭据；用独立 MCP 客户端验收。

普通数据库账号的目标权限是强制安全边界。SQL 解析是额外防护，不替代数据库 GRANT、视图/函数权限及网络隔离。SSH 普通账号也必须由运维确认文件、组、sudo 等权限；连接成功不代表它是低权限账号。

## API-Key / OIDC 配置

本地 Key：`type=api_key, validation_mode=managed`，管理 API 签发，`dbk_...` 原文只显示一次；数据库只存带独立 pepper 的摘要。轮换采用新建 → 替换客户端 → 撤销旧 Key，不在原 Key 上修改权限。默认 Key 不带写入/特权 scope。

外部 Key：`type=api_key, validation_mode=external_http`，配置 `verify_endpoint`、`audiences`、`service_secret`；每次请求在线验证，不信任调用者自报用户名。DeeBee POST：

```json
{"api_key":"<opaque-key>","audience":"deebee","request_id":"<random-id>"}
```

验证服务使用 `Authorization: Bearer <service_secret>` 认证 DeeBee，成功返回：

```json
{"active":true,"subject":"agent-ops","credential_id":"key-123","audience":"deebee","expires_at":"2030-01-01T00:00:00Z","scopes":["resources:read","ssh:exec","db:query"],"client_id":"agent-runtime"}
```

可配置 `field_mapping`，例如 `{"subject":"user.id","scopes":"permissions.scopes"}`。字段只来自可信验证响应，不从请求 body 取身份。服务异常/超时拒绝认证，不退回本地用户。

OIDC：`type=oidc`，`validation_mode=jwt` 或 `introspection`；精确 issuer、audiences、required_scopes，可限制 allowed_client_ids。JWT 接收 RFC 9068 `typ=at+jwt` Access Token，RS256/ES256，验证签名、iss、aud、sub、exp、iat；不接收 ID Token 作为 API 凭据。JWT 缓存 JWKS 最多 5 分钟；新 kid 受限刷新。源端撤销离线 JWT 不会即时生效，需短寿命 Token 或 introspection；DeeBee 本地禁用立即拒绝新请求。

Opaque Token：固定 introspection 地址，`service_client_id` + `service_secret` HTTP Basic；要求 active、可信主体、受众、过期时间与 scopes。JWT 和 opaque 必须为 DeeBee 签发，不能把发给 AgentMesh/SenseMate 的 Token 直接透传。

浏览器 OIDC：额外开启 browser_login、配置 client_id（按需要 client_secret），在 IdP 登记固定回调 `${DEEBEE_PUBLIC_URL}/api/auth/oidc/{source_id}/callback`。授权码 + PKCE S256 + state + nonce；HttpOnly 会话 cookie、写操作 CSRF 校验，不持久化 refresh token，重启后重新登录。

身份源出网固定端点、HTTPS、无自动重定向、响应大小和超时限制；禁止云元数据/链路本地地址。可信企业内网 IdP 需要部署级显式开关。生产还应使用出口防火墙限制目的地址，避免仅依赖应用层 DNS 检查。

## Agent 调用合同

本地 Key 头：`X-DeeBee-API-Key: <key>`。外部 Key 另外传 `X-DeeBee-Identity-Source: <source_id>`。若部署显式设置 `DEEBEE_ACCESS_ALLOW_MANAGED_KEY_BEARER=1`，只支持固定 Bearer 头的客户端也可发送 `Authorization: Bearer <dbk_...>`；Key 仍经过相同的摘要、绑定、scope、资源、有效期和撤销校验。该兼容开关默认关闭，不改变 OIDC Token 路径。

OIDC 头：`Authorization: Bearer <access_token>`。Opaque Token 必须传来源选择器；同 issuer 配置多个来源时 JWT 也须明确选择。只允许一种凭据，禁止重复认证头、URL query 中传 Token，以及客户端提供任意 host/account_id/database。MCP session ID 不是认证凭据。

只支持 Streamable HTTP 与 Bearer 的客户端（例如 WorkBuddy）可使用以下结构；`url` 必须保留末尾 `/`，Key 使用后台仅显示一次的原文：

```json
{
  "deebee": {
    "type": "streamableHttp",
    "url": "http://127.0.0.1:3000/mcp/",
    "headers": {"Authorization": "Bearer <dbk_...>"},
    "timeout": 60000,
    "disabled": false
  }
}
```

| MCP 工具 | REST |
|---|---|
| identity.me | GET `/api/v1/me` |
| resources.list / resources.get | GET `/api/v1/resources` / `/{id}` |
| db.schema | GET `/api/v1/resources/{id}/schema` |
| ssh.exec | POST `/api/v1/ssh/executions` |
| db.query | POST `/api/v1/db/queries` |
| db.execute | POST `/api/v1/db/executions` |
| executions.get / executions.cancel | GET `/api/v1/executions/{id}` / POST `/{id}/cancel` |

```json
{"resource_id":"resource_id_from_directory","mode":"normal","sql":"SELECT id,name FROM items WHERE id=:id","parameters":{"id":1},"max_rows":100,"timeout_seconds":30,"idempotency_key":"unique-business-operation-id"}
```

SSH 用 `command` 替代 sql/parameters。提交返回 execution_id；轮询直到 succeeded/failed/cancelled/unknown。同身份绑定、工具、资源、幂等键同参返回旧任务；不同参数 409。**unknown 不可自动重放修改**，需核实远端状态。

结果分页用服务端签名的 next_cursor；数据库结构支持 table 过滤。数字金额以字符串返回，超大整数字符串化，二进制只返回大小。SQL 是单条保守白名单：查询 SELECT/集合查询；特权业务表 INSERT/UPDATE/DELETE/CREATE/ALTER/DROP TABLE/INDEX。拒绝批量语句、事务命令、账号/角色管理、COPY、文件操作、动态 SQL、未知函数、RETURNING 等。原工作台完整 SQL 能力不受该新接口约束。

限制：单请求 256 KiB；全局 50、每主体 3、每资源 5 个执行并发；默认 60 秒上限（请求默认 30 秒），最大 300 秒；默认输出 1 MiB（最大 2 MiB），默认 1000 行（最大 10000）。取请求、资源、Grant 和凭据寿命中的更紧限制。驱动流式读取；单个超大数据库单元格仍可能短暂占用驱动内存，生产应增加数据库侧和进程内存限制。

本地授权变化触发任务检查，外部在线撤销最长约 30 秒重新校验；新请求每次校验。SSH 断连不保证远端孙进程已退出；数据库非事务 DDL、网络故障与提交竞争可能不可撤销，因此保守返回 unknown。

## 旧连接安全引用与配置导入

“Agent 接入 → 原连接安全接入 → 预览原连接”只读列出支持的连接；“建立/刷新禁用引用”建立资源和待核验账号，**默认特权标签、未启用、无 Grant**。旧连接文件、账号密码和工作台均不改写。数据库连接必须已有明确默认库；V1 不导入隧道/代理连接。

原连接仍是地址/账号/凭据唯一权威来源：引用账号不复制密码，后端通过桥接器读取；新页面不允许覆盖这部分字段。地址/凭据等原配置变化会使核验指纹失效，新外部请求拒绝，运行任务下一次检查取消。管理员刷新引用、补充 TLS/主机指纹、重新测试启用后恢复。新增独立资源则仅由新管理面维护，不回写原连接。

配置 JSON 导出不含秘密；支持新增禁用草稿及更新已有身份、绑定、资源、账号、授权，携带稳定 id 和最新 version。导入预览走同样验证但整体回滚；正式导入也全有或全无，无删除操作。来源先于身份、资源、账号、绑定、Grant，引用必须存在或在同一份配置先行创建。含密钥字段、重复 ID、错误账号归属、过期版本全部拒绝。迁移引用必须先通过专用预览创建，不能用 JSON 伪造关联。参考 `access-mapping.example.json`。

## 数据、备份与回退

独立目录默认 `${connections.json 所在目录}/access-v1`：access.sqlite3、SQLite WAL/SHM、credentials.key、api-key.pepper。目录和密钥限制文件权限；目标凭据及执行 SQL/命令/结果采用独立 Fernet 加密。任何能够同时读数据库与密钥的宿主机管理员仍属于可信边界，不宣称硬件密钥隔离。

执行队列中的完整加密请求及结果 7 天清理，执行元数据/幂等记录保留 90 天；审计事件默认长期保留。`DEEBEE_AUDIT_RETENTION_DAYS=0` 表示不自动删除，正整数表示保留相应天数。超过幂等保留期的旧键不再有防重保证，客户端不得复用。

每个 MCP、授权用户前端、管理端、原工作台业务 API 请求都记录：请求 ID、时间、入口、登录用户/内部身份、身份类型、认证方式、身份源/subject/凭据 ID、客户端、方法与路径、操作、目标资源、实际账号、普通/特权模式、HTTP 状态、耗时和错误码。请求与返回在递归脱敏后分别截断到 1 KiB；密码、Token、API-Key、Cookie、私钥和口令不落审计。SSH/RDP WebSocket 以会话归档，交互式密码提示后的输入会被遮盖。`/api/health`、静态资源和 CORS 预检不是用户业务操作，不写审计。

管理端“审计日志”支持按用户/身份、操作和入口筛选；“打开详情”读取 `/api/admin/v1/audit-events/{id}`，展示身份链、请求和响应。执行详情同时展示仍在 7 天保留期内的解密请求与结果。审计位于本机 SQLite，能满足管理追责与备份，但不是不可篡改外部账本；高合规场景应同步到只追加的远端日志平台。

备份应先停止单实例，再整体备份新目录、旧 connections.json、原 secret.key/DEEBEE_TOKEN_SECRET 与部署配置；不要仅复制正在写入的 sqlite 主文件。恢复应成套恢复密钥与数据，启动后未终结任务标 unknown，不重放。旧连接引用同时依赖原连接密钥。回退前封闭外部入口、停止新执行；保留新审计目录，旧版本继续读未改写的旧连接。测试覆盖了持久密钥重开解密，但不代替运维的异机备份恢复演练。

## 两种产品路线

- 内聚：SenseMate 管身份/Agent 生命周期和编排，AgentMesh 执行任务；其可信运行环境取得面向 DeeBee 的 Access Token 或专属 Key，调用这里的 MCP。最终资源权限仍由 DeeBee 校验，不能把模型声称的 user_id 当委托身份。
- 开放：任意支持自定义认证头的远程 MCP 客户端，或 REST 客户端直连。无需依赖上述两个产品。本分支没有修改它们的代码，也未替用户的具体 Agent 产品完成最终签收。

独立验收脚本 `backend/examples/access_agent_smoke.py`：在安全终端设置 `DEEBEE_MCP_URL`，以及 `DEEBEE_API_KEY` 或 `DEEBEE_ACCESS_TOKEN`（只选一种）；外部 Key/opaque 另设 `DEEBEE_IDENTITY_SOURCE`。运行 `backend/.venv/bin/python backend/examples/access_agent_smoke.py --protocol ssh`（或 mysql/postgresql），可选 `--resource-id`。脚本只做普通模式 id/SELECT 1，不自动申请权限或重放失败命令。

## 隔离实验室复现

所有示例测试口令仅适用于本机实验室。**不要部署测试身份服务到公网，不要对生产 URL 运行测试。**

```sh
docker compose -f backend/tests/access_lab/compose.yml up -d --build --wait
npm run build
# 终端 A：从仓库根目录启动独立后端，不使用原工作台的数据目录。
access_lab_dir=$(mktemp -d /tmp/deebee-access-lab.XXXXXX)
export DEEBEE_BASE_PATH=/ DEEBEE_PUBLIC_URL=http://127.0.0.1:18080
export DEEBEE_CONNECTIONS_FILE="$access_lab_dir/connections.json"
export DEEBEE_ACCESS_DIR="$access_lab_dir/access" DEEBEE_SECRET_FILE="$access_lab_dir/secret.key"
export DEEBEE_ADMIN_USER=admin DEEBEE_ADMIN_PASSWORD=deebee
export DEEBEE_TOKEN_SECRET=access-lab-local-only-session-signing-secret
export DEEBEE_MYSQL_ENABLED=false DEEBEE_POSTGRES_ENABLED=false
export DEEBEE_ACCESS_ALLOW_INSECURE_LOCAL=1 DEEBEE_WEB_DIR="$PWD/dist"
backend/.venv/bin/uvicorn deebee.main:app --app-dir backend --host 127.0.0.1 --port 18080 --no-access-log
```

```sh
# 终端 B：只绑定 loopback 的 IdP / 外部 Key 验证服务。
backend/.venv/bin/uvicorn identity_provider:app --app-dir backend/tests/access_lab --host 127.0.0.1 --port 18991 --no-access-log
```

```sh
# 终端 C：先连接测试，再身份源测试；后者引用前者实际核验的资源。
cd backend
DEEBEE_ACCESS_LIVE_URL=http://127.0.0.1:18080 .venv/bin/pytest tests/test_access_live.py tests/test_access_identity_live.py -q
# 常规回归在根目录：npm test、npm run lint、npm run test:backend。
```

既有 tests/e2e/workbench.spec.ts 含固定旧环境名称和破坏性建删表准备，未直接指向现有实例运行。此次原 UI 回归由真实浏览器在上述隔离后端完成；事务回归由 test_access_live.py 完成。具体证据和未覆盖边界见 `ACCESS_IMPLEMENTATION_LOG.md`。
