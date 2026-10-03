# Kubernetes 功能说明

## 对照基线

本实现以用户提供的 `jumpserver-dev.zip` 为 Core 基线，并对照同版本的 Koko 连接器和 Luna Web 工作台。审查时固定的代码版本为：

- JumpServer Core `95bba960f0038770c870aa95d58aeb8d4032528a`
- Koko `3d7281f0469eaaebe82b1e74132d1183f9699970`
- Luna `409f2f457f7abd2431cf3c8a4cf99eee5856d435`
- Lina `504e15a6dcae517c6f1a111875a9fcd17ea660e8`

重点对照了 Core 的 `apps/assets/const/protocol.py`、Koko 的 `pkg/proxy/k8s.go`、`pkg/srvconn/conn_k8s*.go` 以及 Luna 的 `ui/koko/workspaces/KubernetesWorkspace.vue`。

## 功能对照

| JumpServer Kubernetes 行为 | DeeBee 实现 |
|---|---|
| API Server、Token、可选 Namespace | Kubernetes 连接类型，默认 443，Bearer Token 与可选 Namespace |
| Token 可用性检查 | 读取 `/version` 并通过 `auth can-i get pods --all-namespaces` 检查权限 |
| Namespace → Pod → Container 树 | 同层级资源树，仅包含普通 Container，不把 Init Container 伪装成可登录容器 |
| 无 Namespace 列表权限的受限 ServiceAccount | 优先使用 Context Namespace，再从 ServiceAccount JWT claim 提取 Namespace；全局 Pod 列表被拒绝时逐 Namespace 回退 |
| 资源树刷新、搜索、最近容器 | 支持刷新、全路径搜索、最近 10 个容器及清空 |
| 集群 kubectl Shell | 启动本地 `kubectl proxy`，交互 Shell 只拿到无凭据的 loopback Kubeconfig，运行在 root 容器内时降权为 uid/gid 65534 |
| Pod Container exec | 使用 `kubectl exec -it`，按 bash、sh、PowerShell、cmd 的顺序探测可用 Shell |
| 多个容器子标签 | 集群和容器终端可同时打开，独立输入、输出、状态与关闭生命周期 |
| PTY 窗口变更 | xterm.js FitAddon 与后端 `TIOCSWINSZ` 联动，容器 exec 同样收到窗口尺寸 |
| 终端操作 | 10,000 行回滚、复制、粘贴、清屏、重连、侧栏拖拽调整 |
| 会话审计 | 每个集群/容器子会话单独归档输入、输出摘要、目标和结束状态，复用 DeeBee 脱敏与 UTF-8 字节截断规则 |
| 断线和资源回收 | 终止 PTY 进程组、关闭本地代理、删除临时 Kubeconfig 和 Session 目录 |

DeeBee 另外支持导入完整 Kubeconfig，因此可使用内嵌 CA 和客户端证书/私钥；这是对 JumpServer Token 模式的兼容扩展。为防止导入内容在 DeeBee 服务端执行命令或读取任意文件，`exec`、`auth-provider` 和 CA/证书/私钥的外部文件引用会被拒绝。导入内容使用 DeeBee 现有 Fernet 密钥加密后落盘，不在连接列表或 WebSocket URL 中返回。

JumpServer 整个平台共用的组织、用户授权、会话分享和终端 AI 不是 Kubernetes 连接器本身的协议功能；DeeBee 保留自身的登录、资源池、审计和授权语义，不伪装成 JumpServer 整个 PAM 平台的复制品。

## 真实集群验收

```bash
DEEBEE_KUBECONFIG=/absolute/path/to/config \
DEEBEE_WEB_URL=http://127.0.0.1:8000 \
DEEBEE_API_URL=http://127.0.0.1:8000/api \
npx playwright test tests/e2e/kubernetes.spec.ts
```

若同时设置 `DEEBEE_K8S_CONTAINER=namespace/pod/container`，用例还会打开指定容器终端并执行交互命令。运行时使用独立的临时 DeeBee 数据目录，不要把真实 Kubeconfig 放入仓库。
