# 管理后台交互回归

管理后台的 Arco Select、Checkbox 和日期控件不得包在原生 `label` 中。Select 内部的隐藏输入框会收到 label 转发的第二次点击，导致下拉立即关闭；Checkbox 自带 label，再包一层会产生嵌套标签。使用 `access-field` 作为布局容器，并将可访问名称传到实际输入框（`input-attrs` / `textarea-attrs`）。

身份验证服务按当前协议提交可见字段，避免从 OIDC 切回 API Key 后仍提交隐藏的浏览器登录选项。服务的类型、Issuer 和验证模式由服务端定义为不可变字段，编辑时显示锁定说明。数据库账号只提供密码认证。修改配置 JSON 后必须重新检查导入内容。

## 使用真实后端运行浏览器回归

必须使用独立的非生产实例。测试会创建和修改账号、身份服务、绑定、授权、凭证、资源，并删除本次创建的测试资源。不要把生产 URL 传给测试。

实例需要已有且已核验的资源 `Linux 8.159.155.8`（普通账号 `deebee-mcp`）及 `IDMesh MySQL / idmesh`（普通账号 `deebee_mcp_reader`），并有可回放的 SSH 和 RDP 录像。测试的远端执行仅使用服务器只读检查、数据库结构读取和 `SELECT 1`。

```sh
# 私有 JSON 文件格式 {"username":"...","password":"..."}，权限 0600。
DEEBEE_API_PROXY=http://127.0.0.1:13187 npm run dev -- --host 127.0.0.1
DEEBEE_ADMIN_QA_URL=http://127.0.0.1:5173/deebee/admin \
DEEBEE_ADMIN_QA_CREDENTIALS=/private/path/admin-qa.json \
npx playwright test tests/e2e/admin-live.spec.ts --workers=1
```

可选的 `DEEBEE_ADMIN_QA_IDENTITY` 指向测试 JWT 和 opaque token 的私有 JSON 文件（键 `jwt`、`opaque`）。它们由 `backend/tests/access_lab/identity_provider.py` 产生；该服务只能绑定 QA 容器的 `127.0.0.1:18991`，不能公开发布。启用此项后，额外验证 JWT、Introspection 和外部 API Key 的真实协议与身份绑定。

覆盖内容：所有管理菜单及子页、下拉和类型切换、复选框、分类和筛选、账号新增编辑启停、身份绑定、资源及账号登记、权限预览和有效期、凭证签发及撤销、真实只读执行、审计详情、双协议录像回放下载、配置导出检查导入、同步预览，以及系统账号入口登录和执行。

测试默认跳过，只有显式提供隔离环境地址时才运行；浏览器控制台异常会使测试失败。
