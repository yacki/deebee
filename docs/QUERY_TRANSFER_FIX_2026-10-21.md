# 实际查询回包延迟排查与修复（2026-10-21）

用户报告：页面显示约 300ms 的 SQL，实际等待五六秒才显示结果。本次验证真实生产库 `idmesh` 的只读 `SELECT * FROM core_app`，没有修改业务数据，也没有保存业务结果明细。

## 定位

浏览器只读检查确认当前已加载最新前端 `index-0MKfe-Xn.js`，不是旧版缓存。原来仅用模拟结果验证表格渲染和页签切换，没有覆盖生产结果的完整传输链路。

实际结果为 7179 行、23 列，JSON 约 6,234,554 字节，最大单元格约 81,899 字符。后端 `elapsed_ms` 在查询返回前计算，不包含 FastAPI JSON 响应编码、网关传输、浏览器 JSON 解析和表格显示；MySQL 会话预检也发生在该计时起点之前。

| 测量 | SQL/取数 | 等到响应头 | 下载响应体 | HTTP 总计 |
| --- | ---: | ---: | ---: | ---: |
| 修复前，生产机回环 | 329.3ms | 539.2ms | 42.2ms | 581.4ms |
| 修复前，当前客户端公网 | 319.0ms | 644.1ms | 7117.4ms | 7761.5ms |
| 修复后，客户端公网第 1 次 | 292.0ms | 569.1ms | 1747.8ms | 2317.0ms |
| 修复后，客户端公网第 2 次 | 354.7ms | 730.7ms | 2544.5ms | 3275.1ms |

主因是结果体未经压缩，即使客户端声明 `Accept-Encoding: gzip`，仍返回 identity 编码，约 6.23MB 经公网下载。数据表格的虚拟渲染不能减少这部分传输。

## 已发布修复

只在网关的 DeeBee location 中增加 JSON 压缩，压缩级别 5，最小体积 1024 字节，正确返回 `Vary: Accept-Encoding`。配置先备份并用 `nginx -t` 验证候选，再原位写入挂载文件和平滑 reload。共享网关的其他 location 和所有代理转发/升级/超时指令保持原样；应用源代码、前端构建、Compose、密钥及信任文件均未修改。DeeBee、guacd 和网关容器未重启。

实际传输体缩至约 1.72MB，减少约 72.4%。配置位于 `/data/ocp-install-e2e-r5/nginx/nginx.conf`；备份位于 `/data/deebee/releases/query-transfer-20261021/nginx.conf.before`。

生产 location 的最终内容：

```nginx
    location ^~ /deebee/ {
      # Compress DeeBee JSON results; event streams and WebSocket upgrades bypass gzip.
      gzip on;
      gzip_types application/json;
      gzip_min_length 1024;
      gzip_comp_level 5;
      gzip_vary on;
      gzip_proxied any;
      proxy_pass              http://deebee:3000;
      proxy_http_version      1.1;
      proxy_set_header        Host              $host;
      proxy_set_header        X-Real-IP         $remote_addr;
      proxy_set_header        X-Forwarded-For   $proxy_add_x_forwarded_for;
      proxy_set_header        X-Forwarded-Proto $scheme;
      proxy_set_header        Upgrade           $http_upgrade;
      proxy_set_header        Connection        $connection_upgrade;
      proxy_request_buffering off;
      proxy_buffering         off;
      proxy_cache             off;
      proxy_read_timeout      3600s;
      proxy_send_timeout      3600s;
    }
```

`application/json` 之外只保留 Nginx 默认的 HTML 压缩类型，SSE 的 `text/event-stream` 不在压缩类型中，WebSocket 升级指令未改动。本次没有新建真实 SSH/RDP 会话来声称完整远程连接回归。

## 验证与剩余限制

- 公网真实请求确认 `Content-Encoding: gzip`；声明 identity 时仍返回未压缩 JSON。
- 两种传输返回的完整 results 哈希一致，7179 行、23 列、所有字段值保持一致，没有通过丢行或截短值减少体积。
- 小健康响应不压缩，健康正常；未登录仍按现有接口契约拒绝（旧管理员接口为 HTTP 400，“登录已失效”）。
- 在用户当前浏览器运行已有只读 SQL，完整结果显示成功，挂载 40 个虚拟行；外部计时约 3223ms，包含浏览器自动化开销，不能当作精确浏览器内部首绘指标。已恢复原“摘要”页签。
- 未提交或推送 Git；`git diff --check` 通过。本次只修改运行网关配置，没有重复执行与此无关的前端构建或组件测试。
- 压缩后当前链路仍需 2.3–3.3 秒。1.72MB 结果仍要完整接收、解析后才能展示；本次没有引入分批加载或首屏流式返回，因此不能宣称端到端已经接近 SQL 的 300ms。

证据：`docs/qa/2026-10-21/query-transfer-results.json`、`query-transfer-deployment.json`、`query-transfer-browser-summary.png`。不保存实际业务结果正文。
