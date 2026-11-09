# SQL 补全框留白与居中修复

2026-10-22 00:26（Australia/Perth）已发布到 https://deebee.idmesh.site/deebee/ ，刷新工作台生效。

侧栏使用的全局 `.tree` 样式同时匹配了 Monaco 补全框内部元素，带入 `padding: 2px 0 8px`，将 30px 候选行的可见区域压缩到 22px。修复前实测：上方留白 3px、下方 7px，文字中心向下偏移 3.5px、图标偏移 4px。

运行时代码只修改 `src/styles.css`，将这条样式限定为 `.explorer .tree`。侧栏布局保留原值，补全框不再被侧栏样式影响，候选行完整显示，文字和图标恢复垂直居中。

浏览器检查直接测量候选行与可见区域的交集、文字和图标的位置，不只检查元素是否存在：

- 候选行 30px 全部可见，无裁切。
- 首行上方和末行下方留白差不超过 0.5 CSS px。
- 左侧名称、右侧说明及图标的中心误差均不超过 1 CSS px。
- 覆盖单个、多个候选；1440、1024、800 像素宽窗口；DPR 1 和 DPR 2。
- 新增布局检查先在旧构建上复现失败；修复后本地 26 个浏览器用例通过，线上静态资源下 21 个补全用例通过。
- 多标签用例同步等待新查询标签和默认 SQL 显示，再输入测试内容，避免模拟会话异步创建造成测试抢先操作旧标签。
- 生产构建、相关测试 ESLint、`git diff --check` 通过。

发布只热更新前端。66 个容器内文件及 65 个公网页面/静态文件校验通过；130 个旧哈希资源保持一致。后端容器 ID、启动时间、Python 源码、guacd 和网关均未改变；环境、密钥、连接及信任文件哈希一致。浏览器使用模拟 API，没有执行真实数据库查询或写入。未创建 Git 提交，未推送代码或镜像。

备份：`/data/deebee/releases/completion-alignment-20261022-002521/backup/`。下次容器重建使用镜像 `deebee-local:ui-completion-alignment-20261022-002521-ae7299fd`。

[发布记录](qa/2026-10-22/completion-alignment/deployment.json)，[修复前](qa/2026-10-22/completion-alignment/before.png)，[修复后单行高分屏](qa/2026-10-22/completion-alignment/after-single-dpr2.png)，[修复后多行高分屏](qa/2026-10-22/completion-alignment/after-multiple-dpr2.png)。
