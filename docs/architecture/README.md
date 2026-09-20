# Agent安全家族全景架构图

## 当前交付：V4 Sense Agent 全景展示版

2026-09-20，按用户最终审阅意见修改。

- [SVG 矢量原稿](sense-agent-security-family-v4.svg)
- [高清 PNG，4800 × 7168](sense-agent-security-family-v4-4800.png)
- [标准预览](sense-agent-security-family-v4-preview.png)
- [排版检查结果](sense-agent-security-family-v4-qa.json)

标题改为居中的“Sense Agent 安全家族全景架构图”；删除副标题、右上角状态框、规划/待联调等状态注记及对应状态说明卡片、特权条件提示条；IDMesh 区域统一展示为“IDMesh · 统一身份认证与访问控制”，不展示内部项目目录名；资源区调整为 Linux 主机、MySQL/Postgres 数据库、Redis 缓存、K8s 集群四栏。其余主体内容与配色保持 V3。

这是产品全景展示文件的更新，不是实现或发布声明；新增资源类别表达产品覆盖方向，本轮没有修改后端驱动、授权逻辑或任何产品功能。实现范围仍以 ACCESS_V1.md 和测试记录为准。

渲染：`node docs/architecture/render-family-v3.mjs sense-agent-security-family-v4`。自动检查 166 个文字元素、40 个卡片，无文字重叠或越界；已目视检查整图。保留此前所有版本。

## 历史版本：V3 原生 SVG

按用户明确要求改回 SVG 代码排版，沿用 V2 扁平分层版的浅蓝、浅青、淡紫与浅琥珀配色。不使用图片模型生成图形或文字，不覆盖早期版本。

- [SVG 矢量原稿，可持续放大与编辑](agent-security-family-flat-v3.svg)
- [高清 PNG，4800 × 7168](agent-security-family-flat-v3-4800.png)
- [标准预览，2400 × 3584](agent-security-family-flat-v3-preview.png)
- [排版检查结果](agent-security-family-flat-v3-qa.json)
- [可重复渲染脚本](render-family-v3.mjs)

技术：直接编写 SVG + CSS，使用 Playwright / Chromium 渲染，高清 PNG 以 2× 像素密度直接从矢量图导出，不放大低清位图。SVG 无外部图片、脚本或网络依赖；字体优先使用本机苹方，其他系统使用中文字体回退。

检查：181 个原生文字元素、42 个内容卡片；文字越界、卡片溢出、文字互相重叠检查均通过。已查看整图及使用者/工具生态、身份映射/特权、身份底座/双路线三处原尺寸局部；已修正目标卡片底部留白及身份映射连线穿过小标题的问题。

仅新增图稿、渲染脚本、导出图与说明；不改变产品功能、界面或部署。图中跨产品连接仍为待联调的目标关系，右侧 AI Agent 安全明确为规划建议。

## 历史版本：V2 图片生成方案

2026-09-19 · V2 · 双风格方案全景图

- [扁平分层版](agent-security-family-flat-v2.png)
- [科技轻 2.5D 蓝紫版](agent-security-family-blue-purple-v2.png)
- [内容规格与完整生成提示词](agent-security-family-v2-prompts.md)

两图由内置 image_gen 生成，保存原始输出，未以程序放大。当前原生尺寸均为 1024 × 1536。原有 V1 图未覆盖，产品功能和界面未修改。

## 阅读边界

这是产品家族目标架构，不是已完成的跨产品部署拓扑。IDMesh、SenseMate、AgentMesh、DeeBee 各自已有能力依据本地代码；跨产品信任、统一体验、审计关联仍需联调。右侧 AI Agent 安全为规划建议，不代表完整安全平台已实现。

IDMesh 作为身份基础向上支撑各产品；位于底部不表示数据库执行之后调用 IDMesh。调用链为用户/Agent → 可选 AgentMesh 治理 → DeeBee 最终鉴权授权 → SSH/MySQL/PostgreSQL。其他 MCP/API 服务不属于 DeeBee 执行核心。本地 Tools 由 SenseMate 本地策略和沙箱约束，并非自动经过 AgentMesh。

## 图像验收记录

已逐图检查：标题、使用者角色、四个产品边界、Tools/MCP、本地与外部身份源、账号映射、特权勾选、目标资源、身份底座、安全规划和两条路线均可见。第一版为扁平卡片布局；第二版为蓝紫浅立体平台与图标，文字保持正面。

局限：全景图文字密度较高；生成图存在技术标点和小字简写，不能作为逐字 API 合同。身份支撑总线和管理控制线也是概念表达，不是逐端点协议图。尚未进行用户批准后的精修。

尤其以以下精确说明为准：

- 外部 Token 必须按配置校验 issuer、audience、subject、有效期及 scopes；图中摘要可能未逐项展示。API 使用 Access Token，不使用 ID Token。
- IdentityBinding 使用受信身份源的 source_id + subject 显式映射到 principal_id，不接受请求自报 userId 作为授权依据。
- 特权 scope 精确拼写为 `privilege:use`，不是 `privilegeuse`。后台勾选、特权账号核验启用、授权动作和凭据 scope 均满足、请求显式 `mode=privileged` 才能使用特权账号；数据库写入另需 `db:write`。
- 特权目标账号与后台管理员角色不同。勾选不自动创建 root 权限，不自动 sudo，不改变原本地登录。
- DeeBee V1 没有逐次审批。右侧高风险审批是安全协同规划，不是当前执行前置条件。
- 本地托管 API-Key 与可信外部 API-Key 是独立验证路径；不推断 IDMesh 已支持机器 client_credentials 或用户代理 Token Exchange。
- 图中 `executions.get/cancel` 是展示缩写，实际工具为 `executions.get` 和 `executions.cancel`。

详细实施边界参见 [ACCESS_V1.md](../ACCESS_V1.md)。
