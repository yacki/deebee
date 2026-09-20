# Agent安全家族全景架构图 · 双风格生成说明

日期：2026-09-19

本次按用户要求直接生成两版：原图扁平白皮书风、科技轻 2.5D 蓝紫风。使用内置 image_gen。两版采用同一内容规格。跨产品集成均为目标方案，不代表联调完成；AI Agent 安全是规划建议。

代码依据：
- IDMesh Lite：`/Users/yacki/Projects/Sense/develop/iam-lite`，OIDC/OAuth2、Discovery、JWKS、UserInfo 等协议实现。
- SenseMate：`/Users/yacki/Projects/YClaw`，Agent、Tools、Skills、MCP 客户端、本地策略与沙箱。
- AgentMesh：`/Users/yacki/Projects/Sense/develop/agentmesh-console-backend`，Console/Gateway、NATIVE_MCP、HTTP_TO_MCP。
- DeeBee：`docs/ACCESS_V1.md`，身份映射、资源账号授权、特权执行、MCP/REST。

## 第一版完整提示词

Use case: infographic-diagram. Create a very large, polished Chinese enterprise solution architecture diagram, not a decorative poster. Single portrait image, requested 2304x3456 or comparable highest clear resolution. Exact title: "Agent安全家族全景架构图". Subtitle: "身份底座 · Agent治理 · 工具生态 · 受控执行". One coherent panoramic diagram with crisp simplified Chinese and accurate English product names. Dense but highly readable. All text front-facing. Do NOT reproduce outdated old title or old status text from reference.
Content shared by both versions:
Visual legend beneath title: "能力以代码为依据｜跨产品集成待联调｜虚线＝规划或待接入". This is a TARGET FAMILY ARCHITECTURE not a claim that whole system is production integrated.

LAYOUT: generous page margins, top-down 7 horizontal zones, plus right security vertical sidebar spanning middle zones; a full-width foundational band near bottom. Orthogonal arrows, short line labels, avoid crossing text. Main zones use ~80% width, right rail ~18%, bottom foundation and footer full width. Dominant central detailed DeeBee card. Strong hierarchy, short text; use all core content below; do not substitute generic AI blocks.

ZONE 1 "01 使用者与入口", 3 cards:
"管理员" / "配置身份源、映射、资源与账号" / "授予或撤销特权 · 查看审计"
"普通运维人员" / "人工工作台或 AI 辅助操作" / "仅发现和使用已授权资源"
"外部 Agent / 自动化" / "API-Key 或 OAuth Access Token" / "MCP / REST 接入"
Role separation: a thin control-flow line from admin to AgentMesh control plane and DeeBee authorization config, labeled "管理配置"; operations line from operations user to SenseMate and human DeeBee workbench. Admin management role does not automatically grant target privilege.

ZONE 2 "02 Agent应用与连接治理", 2 cards with clear left-to-right invocation:
"SenseMate · AI任务执行" / "对话与任务计划 · 主/子 Agent" / "Skills · Memory · 本地策略/沙箱" / "MCP Client · Tools 调用"
"AgentMesh · 连接与治理" / "Agent身份与凭据 · 工具目录" / "资源策略 · 模型接入 · 调用审计" / "Console 控制面 → Gateway 数据面"
Arrow SenseMate → AgentMesh labeled "受控调用（待联调）", dashed because planned interproduct integration. External Agents connect to AgentMesh too. Label note: "SenseMate 发起任务；AgentMesh 代理与治理，不代替最终授权".

ZONE 3 "03 Tools / MCP生态", 4 clearly separate tool cards served/routed by AgentMesh:
"原生 MCP 服务" / "发现工具 · Schema · 调用代理"
"HTTP API → MCP" / "业务 API 工具化"
"DeeBee MCP / REST" / "SSH · MySQL · PostgreSQL"
"模型与业务生态" / "LLM · 知识检索 · 企业系统"
Use arrow AgentMesh → these cards labeled "目录 / 路由 / 准入". Business ecosystem is illustrative integration choices, tiny label "生态接入示例".
Small separate SenseMate local tool branch: "本地 Tools / Skills → Policy / Sandbox（不默认经过网关）". This local branch MUST NOT create a bypass to managed DeeBee resources.
DeeBee tool card has prominent downward arrow to Zone 4. Other MCP/API cards must visibly remain outside DeeBee box.

ZONE 4 large central card "04 DeeBee · 身份映射与受控特权执行", nested 4 horizontal subrows:
A. "身份验证（两种来源）" two subcards:
"外部身份源模式" / "IDMesh / 可信 OIDC：JWT 或 Introspection" / "可信外部 API-Key：固定验证服务" / "校验 issuer / audience / sub / 有效期"
"本地身份源模式" / "本地用户密码 → 本地主体" / "DeeBee API-Key：摘要校验 / 到期 / 撤销" / "原本地登录保留，不依赖 IDMesh"
B. "身份映射" left card "IdentityBinding" / "source_id + subject → principal_id" / "显式绑定；无映射拒绝"
→ middle card "Principal · 内部身份" / "人员 / 服务主体" / "统一承载授权与审计"
→ right card "AccessGrant · 主体 × 资源" / "绑定普通账号 / 可选特权账号" / "授权动作与使用限制"
C. "账号与特权" three cards:
"ResourceAccount" / "目标系统真实账号" / "普通账号｜特权账号" / "与后台管理员角色分离"
"□ 允许使用特权账号" / "默认关闭，逐资源勾选" / "授权与凭据均含 privilege:use" / "显式 mode=privileged"
"可用资源投射" / "资源 / 账号名 / 模式 / 动作" / "只返回已授权范围" / "不返回密码或私钥"
Small clear note below: "特权须同时满足全部条件；数据库写入另需 db:write；不自动 sudo；V1 无逐次审批"
D. "共享执行核心" three cards:
"最终授权与账号解析" / "执行前复核身份、资源、动作" / "UI / REST / MCP 复用"
"执行器与任务状态" / "SSH 非交互命令 · SQL" / "超时 / 限额 / 幂等 / 取消"
"加密凭据库" / "DB 密码 / SSH 私钥" / "仅后端取用，不进模型"
A → B → C → D overall downward flow, secret vault short arrow to executor "按引用取用".
Small MCP tool strip at base within card: "identity.me · resources.list · db.schema · db.query · db.execute · ssh.exec · executions.get/cancel"
No claim delegated user token exchange already implemented.

ZONE 5 "05 目标资源与账号边界", 3 cards reached only by DeeBee executor:
"SSH · Linux" / "普通 OS 账号 / 指定特权账号" / "受限 sudo 由目标机配置" / "普通账号不等于只读"
"MySQL" / "只读账号 / 指定变更账号" / "数据库原生权限兜底"
"PostgreSQL" / "只读角色 / 指定变更角色" / "数据库与对象权限兜底"
Note "本版仅 SSH / MySQL / PostgreSQL，不含 RDP". Target resource row sits ABOVE IDMesh foundation but no execution arrow enters IDMesh.

RIGHT VERTICAL RAIL spanning zones2-5: dashed purple border, heading "AI Agent安全", prominent tag "规划中 · 建议能力", six separated compact tiles:
"输入与上下文" / "提示注入检测 · 信任分区"
"工具与供应链" / "工具准入 · Schema漂移"
"调用与权限" / "越权阻断 · 高风险审批"
"运行时约束" / "沙箱隔离 · 网络出口"
"数据与输出" / "脱敏 · 泄露防护"
"持续安全运营" / "行为审计 · 告警处置"
Footnote in rail: "跨产品安全协同待建设；不把提示词当权限边界". Dashed short control links to SenseMate, AgentMesh, DeeBee. Existing local safety components do not mean this whole planned platform is done. High-risk approval here is planned only, must not appear implemented in DeeBeeV1.

ZONE 6 FULL-WIDTH FOUNDATION, distinctly visually grounded: "06 IDMesh · 统一身份基础（iam-lite）".
Four equal modules: "人员 / 组织 / 应用" ; "OIDC / OAuth2" ; "Discovery / JWKS" ; "UserInfo / Token验证".
Below modules: "向上提供可信身份与令牌验证；各产品保留自身授权边界".
Draw BLUE dashed support bus rising up left exterior margin, branching to SenseMate, AgentMesh, DeeBee external identity source; label "身份信任接入 · 待联调". Clearly no raw DB password in IDMesh foundation, no arrow implying IDMesh is target after database.
Important foundation note "API 使用 Access Token，非 ID Token；服务身份/API-Key 独立配置". Don't claim unverified IDMesh machine client_credentials or token exchange support.

ZONE 7 FULL-WIDTH bottom "两条产品路线 · 同一执行内核" with two small cards:
"内聚路线" / "统一体验：SenseMate + AgentMesh + DeeBee" / "IDMesh 提供统一身份基础"
"开放路线" / "外部 Agent → DeeBee MCP / REST" / "可直连；AgentMesh 治理可选；本地身份可独立运行"
Draw bypass route from external Agent via perimeter to DeeBee access, label "开放直连仍需完整鉴权授权" not bypassing auth.
Very bottom short audit ribbon "可追溯链：外部 subject → 内部 Principal → 资源 / 目标账号 → 动作 / 结果" and "跨产品 trace 关联待联调 · 凭据不进入模型 · 网关允许 ≠ 目标允许".

Mandatory architecture: identity foundation supports ecosystem, it is NOT a sequential operation after database. Authentication vs authorization distinct. Target credentials are not OAuth credentials. Both routes share same DeeBee core. All mandatory Chinese names exact; compress explanatory wording only if necessary to prevent tiny text. No invented product names or protocols.
STYLE VERSION A: match the provided original reference image's flat enterprise technical-whitepaper style, using its palette, typography and tidy layered organization but redesign and extend content into requested big panorama. Reference image is STYLE only, not old facts. Cool near-white background #F7F9FC; dark navy headings; subtle pale indigo, pale teal, pale amber group backgrounds; white inner cards, thin blue-gray borders, modest corner radii. Flat orthogonal arrows; no fake 3D; restrained spacious grid; thin indigo top accent. Clear Chinese sans-serif. Simple line role icons optional. Flat, precise and sober, as readable as a carefully typeset technical document.

## 第二版完整提示词

Use case: infographic-diagram. Create a very large, polished Chinese enterprise solution architecture diagram, not a decorative poster. Single portrait image, requested 2304x3456 or comparable highest clear resolution. Exact title: "Agent安全家族全景架构图". Subtitle: "身份底座 · Agent治理 · 工具生态 · 受控执行". One coherent panoramic diagram with crisp simplified Chinese and accurate English product names. Dense but highly readable. All text front-facing. Do NOT reproduce outdated old title or old status text from reference.
Content shared by both versions:
Visual legend beneath title: "能力以代码为依据｜跨产品集成待联调｜虚线＝规划或待接入". This is a TARGET FAMILY ARCHITECTURE not a claim that whole system is production integrated.

LAYOUT: generous page margins, top-down 7 horizontal zones, plus right security vertical sidebar spanning middle zones; a full-width foundational band near bottom. Orthogonal arrows, short line labels, avoid crossing text. Main zones use ~80% width, right rail ~18%, bottom foundation and footer full width. Dominant central detailed DeeBee card. Strong hierarchy, short text; use all core content below; do not substitute generic AI blocks.

ZONE 1 "01 使用者与入口", 3 cards:
"管理员" / "配置身份源、映射、资源与账号" / "授予或撤销特权 · 查看审计"
"普通运维人员" / "人工工作台或 AI 辅助操作" / "仅发现和使用已授权资源"
"外部 Agent / 自动化" / "API-Key 或 OAuth Access Token" / "MCP / REST 接入"
Role separation: a thin control-flow line from admin to AgentMesh control plane and DeeBee authorization config, labeled "管理配置"; operations line from operations user to SenseMate and human DeeBee workbench. Admin management role does not automatically grant target privilege.

ZONE 2 "02 Agent应用与连接治理", 2 cards with clear left-to-right invocation:
"SenseMate · AI任务执行" / "对话与任务计划 · 主/子 Agent" / "Skills · Memory · 本地策略/沙箱" / "MCP Client · Tools 调用"
"AgentMesh · 连接与治理" / "Agent身份与凭据 · 工具目录" / "资源策略 · 模型接入 · 调用审计" / "Console 控制面 → Gateway 数据面"
Arrow SenseMate → AgentMesh labeled "受控调用（待联调）", dashed because planned interproduct integration. External Agents connect to AgentMesh too. Label note: "SenseMate 发起任务；AgentMesh 代理与治理，不代替最终授权".

ZONE 3 "03 Tools / MCP生态", 4 clearly separate tool cards served/routed by AgentMesh:
"原生 MCP 服务" / "发现工具 · Schema · 调用代理"
"HTTP API → MCP" / "业务 API 工具化"
"DeeBee MCP / REST" / "SSH · MySQL · PostgreSQL"
"模型与业务生态" / "LLM · 知识检索 · 企业系统"
Use arrow AgentMesh → these cards labeled "目录 / 路由 / 准入". Business ecosystem is illustrative integration choices, tiny label "生态接入示例".
Small separate SenseMate local tool branch: "本地 Tools / Skills → Policy / Sandbox（不默认经过网关）". This local branch MUST NOT create a bypass to managed DeeBee resources.
DeeBee tool card has prominent downward arrow to Zone 4. Other MCP/API cards must visibly remain outside DeeBee box.

ZONE 4 large central card "04 DeeBee · 身份映射与受控特权执行", nested 4 horizontal subrows:
A. "身份验证（两种来源）" two subcards:
"外部身份源模式" / "IDMesh / 可信 OIDC：JWT 或 Introspection" / "可信外部 API-Key：固定验证服务" / "校验 issuer / audience / sub / 有效期"
"本地身份源模式" / "本地用户密码 → 本地主体" / "DeeBee API-Key：摘要校验 / 到期 / 撤销" / "原本地登录保留，不依赖 IDMesh"
B. "身份映射" left card "IdentityBinding" / "source_id + subject → principal_id" / "显式绑定；无映射拒绝"
→ middle card "Principal · 内部身份" / "人员 / 服务主体" / "统一承载授权与审计"
→ right card "AccessGrant · 主体 × 资源" / "绑定普通账号 / 可选特权账号" / "授权动作与使用限制"
C. "账号与特权" three cards:
"ResourceAccount" / "目标系统真实账号" / "普通账号｜特权账号" / "与后台管理员角色分离"
"□ 允许使用特权账号" / "默认关闭，逐资源勾选" / "授权与凭据均含 privilege:use" / "显式 mode=privileged"
"可用资源投射" / "资源 / 账号名 / 模式 / 动作" / "只返回已授权范围" / "不返回密码或私钥"
Small clear note below: "特权须同时满足全部条件；数据库写入另需 db:write；不自动 sudo；V1 无逐次审批"
D. "共享执行核心" three cards:
"最终授权与账号解析" / "执行前复核身份、资源、动作" / "UI / REST / MCP 复用"
"执行器与任务状态" / "SSH 非交互命令 · SQL" / "超时 / 限额 / 幂等 / 取消"
"加密凭据库" / "DB 密码 / SSH 私钥" / "仅后端取用，不进模型"
A → B → C → D overall downward flow, secret vault short arrow to executor "按引用取用".
Small MCP tool strip at base within card: "identity.me · resources.list · db.schema · db.query · db.execute · ssh.exec · executions.get/cancel"
No claim delegated user token exchange already implemented.

ZONE 5 "05 目标资源与账号边界", 3 cards reached only by DeeBee executor:
"SSH · Linux" / "普通 OS 账号 / 指定特权账号" / "受限 sudo 由目标机配置" / "普通账号不等于只读"
"MySQL" / "只读账号 / 指定变更账号" / "数据库原生权限兜底"
"PostgreSQL" / "只读角色 / 指定变更角色" / "数据库与对象权限兜底"
Note "本版仅 SSH / MySQL / PostgreSQL，不含 RDP". Target resource row sits ABOVE IDMesh foundation but no execution arrow enters IDMesh.

RIGHT VERTICAL RAIL spanning zones2-5: dashed purple border, heading "AI Agent安全", prominent tag "规划中 · 建议能力", six separated compact tiles:
"输入与上下文" / "提示注入检测 · 信任分区"
"工具与供应链" / "工具准入 · Schema漂移"
"调用与权限" / "越权阻断 · 高风险审批"
"运行时约束" / "沙箱隔离 · 网络出口"
"数据与输出" / "脱敏 · 泄露防护"
"持续安全运营" / "行为审计 · 告警处置"
Footnote in rail: "跨产品安全协同待建设；不把提示词当权限边界". Dashed short control links to SenseMate, AgentMesh, DeeBee. Existing local safety components do not mean this whole planned platform is done. High-risk approval here is planned only, must not appear implemented in DeeBeeV1.

ZONE 6 FULL-WIDTH FOUNDATION, distinctly visually grounded: "06 IDMesh · 统一身份基础（iam-lite）".
Four equal modules: "人员 / 组织 / 应用" ; "OIDC / OAuth2" ; "Discovery / JWKS" ; "UserInfo / Token验证".
Below modules: "向上提供可信身份与令牌验证；各产品保留自身授权边界".
Draw BLUE dashed support bus rising up left exterior margin, branching to SenseMate, AgentMesh, DeeBee external identity source; label "身份信任接入 · 待联调". Clearly no raw DB password in IDMesh foundation, no arrow implying IDMesh is target after database.
Important foundation note "API 使用 Access Token，非 ID Token；服务身份/API-Key 独立配置". Don't claim unverified IDMesh machine client_credentials or token exchange support.

ZONE 7 FULL-WIDTH bottom "两条产品路线 · 同一执行内核" with two small cards:
"内聚路线" / "统一体验：SenseMate + AgentMesh + DeeBee" / "IDMesh 提供统一身份基础"
"开放路线" / "外部 Agent → DeeBee MCP / REST" / "可直连；AgentMesh 治理可选；本地身份可独立运行"
Draw bypass route from external Agent via perimeter to DeeBee access, label "开放直连仍需完整鉴权授权" not bypassing auth.
Very bottom short audit ribbon "可追溯链：外部 subject → 内部 Principal → 资源 / 目标账号 → 动作 / 结果" and "跨产品 trace 关联待联调 · 凭据不进入模型 · 网关允许 ≠ 目标允许".

Mandatory architecture: identity foundation supports ecosystem, it is NOT a sequential operation after database. Authentication vs authorization distinct. Target credentials are not OAuth credentials. Both routes share same DeeBee core. All mandatory Chinese names exact; compress explanatory wording only if necessary to prevent tiny text. No invented product names or protocols.
STYLE VERSION B: "科技轻2.5D蓝紫风格". Preserve EXACT SAME product boundaries, technical content, labels, ordered zones and relationships as version A, but render a sophisticated light 2.5D blue-violet enterprise solution panorama. Very light icy blue-white background, azure/cobalt/soft violet palette, translucent layered platform cards, shallow extruded edges, extremely restrained light gradients, soft ambient shadows. Small elegant isometric shield, person, bot, gateway, server, database and key icons integrated at headings only; NO huge decorative robot. IDMesh foundation like a solid elegant wide blue-purple platform base. Content panels remain front-facing and text horizontal, never perspective-skewed. More visual depth than A but no giant stage or waste of space. AI security rail in faint lavender with dashed outline. Primary data arrows cobalt, identity support paths cyan dashed, planned connections violet dashed. Every important label readable, comparable density to A, not a promotional poster, no neon black background, no excessive glow.

