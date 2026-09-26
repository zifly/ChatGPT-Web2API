# 使用统计页面 / Usage dashboard

更新：2026-09-27。[README](../README.md) · [API 接入](../API使用与项目接入手册.md) · [请求进度](REQUEST-PROGRESS.md)

## 打开页面

更新服务后，在 API 地址后加 `/stats`，例如 `http://NAS-HOST:11111/stats`。页面由现有 REST 服务提供，不需要额外前端服务、CDN 或数据库服务。输入服务 API Key 后查看**该密钥**的统计。默认勾选“在这台设备上记住 7 天”，首次成功读取统计后，将 Key 和固定到期时间保存在当前浏览器、当前站点的 `localStorage`；刷新或重新打开页面会自动恢复，查询不会延长有效期。到期再次访问或仍打开的页面检查到期后清除，点击退出或收到 401 也会清除；退出会同步到其他已打开并使用保存登录的统计页。

共用设备可取消勾选，此时 Key 只在当前页面内存中使用，刷新后需重新输入。浏览器禁止本地存储时会提示并退回这种方式。Key 不写入网址或统计数据库；7 天是浏览器记住登录的期限，不会更改服务 Key 自身的有效性。无密钥部署仍遵循原服务的匿名访问范围。

页面提供今日、近 7 天、近 30 天切换，默认每 5 秒刷新。内容包括完成调用数、成功率、平均/P95 耗时、请求与响应正文流量、当前执行/排队数量、每日趋势、阶段平均耗时，以及所选时间范围内最近 50 条调用。状态筛选只作用于这 50 条记录，不改变顶部汇总。上海时间（UTC+8）00:00 划分日期。

“最近调用”只包含已结束的尝试：成功显示“回复已完成”，失败或取消显示发生阶段，例如“回复处理时失败”“打开会话时取消”。原始 `phase: reply` 代表最后处理回复的阶段，不能据此判断仍在等待；当前未结束的请求看上方“执行中 / 排队中”。

**首次启用后开始记录，不会把旧日志自动导入。** 本地预览中的合成数据不写入线上历史。2026-09-26 已完成 NAS 部署和一条真实测试调用：页面资源与鉴权正常，进度轮询不计入调用，正文流量准确，记录已写入持久化数据库。

## 统计口径

| 指标 | 含义 |
|---|---|
| 完成调用 | 通过鉴权、进入 REST 聊天接口且已结束的 HTTP 尝试，包含成功、失败和取消；参数错误也计为失败 |
| 不计入 | 鉴权失败、健康检查、进度查询、统计页面/API、模型/项目查询，以及 MCP 请求 |
| 成功率 | 成功数 / 全部已结束尝试，分母包含取消；没有记录时显示 `—` |
| 平均/P95 耗时 | 全部已结束尝试的服务端处理时长，包含排队；P95 为排序后第 `ceil(0.95 × 数量)` 个值，空范围为 `null` |
| 日期归属 | 按尝试开始时间归属上海日期；运行中的请求尚未进入历史汇总 |
| 实时执行/排队 | 当前 REST 进程、当前密钥的在途请求；浏览器槽位数是服务配置的总容量 |
| 阶段平均耗时 | 对实际进入过该阶段的已结束尝试取平均，同一尝试重复进入该阶段时累计 |
| 请求流量 | aiohttp 接收的聊天请求正文大小，压缩输入按解压后大小；包含 Base64 等 JSON 包装 |
| 响应流量 | JSON 响应正文大小，或成功交给 aiohttp 的 SSE 帧字节数（包括错误帧和 DONE）；断线时可能只有部分帧 |
| 不属于流量指标 | HTTP/TLS/TCP 头、压缩后的线路流量、浏览器加载网页及访问 ChatGPT 的网络流量、Token 数、费用 |
| 准备重试/回复恢复 | 现有单次请求诊断中的准备重试次数和读取恢复次数；不是网关新会话替换重试次数 |

SSE 即使 HTTP 状态为 200，若本轮以错误结束仍计失败。取消和超时分别计为取消、失败。响应字节数不能证明客户端已经完整收到或业务侧采用了回答。

网关放弃旧会话再发新请求，会产生两条尝试记录，**不能据此推断两个独立业务任务**。服务端目前没有业务任务 ID，因此不做跨请求去重，也不统计“网关替换重试次数”。旧的 `usage.prompt_tokens` 等字段仍为零占位，本页不会把它们显示为真实消耗。

## 保存与隔离

- 使用 SQLite，默认路径 `~/.chatgpt-web2api/usage.sqlite3`，可用 `W2A_USAGE_DB_PATH` 或配置字段 `usage_db_path` 覆盖。
- NAS 的两种 Compose 启动方式都挂载 `./data/usage:/data/usage`，数据库位于 `/data/usage/usage.sqlite3`，替换容器后仍保留。首次升级前在项目目录执行 `mkdir -p data/usage`；部分 NAS 不会自动创建挂载源目录，缺少目录会导致容器无法启动。
- `W2A_USAGE_RETENTION_DAYS` / `usage_retention_days` 默认 90，允许 1–3650 天。启动时清理过期记录，之后有写入时至多每小时清理一次。查询始终排除保留期以外的记录；删除记录不保证 SQLite 文件立即缩小。
- 只保存服务端请求 ID、密钥摘要、时间、结果、耗时、阶段、字节数和恢复次数。不保存聊天正文、图片、回答、真实会话 ID、IP、原始密钥或错误文本。
- 不同服务 API Key 的历史和实时计数隔离；共用同一 Key 的业务用户共用统计范围。更换 Key 会切换到新的统计范围，旧 Key 的记录仍按原保留期清理。
- 记录在独立线程中异步写入，最多排队 1024 条；聊天完成后统计可能略晚出现。磁盘失败或队列满不会改变聊天结果，页面提示本次进程启动后的漏记数量。进程突然被终止时，未提交的记录及尚未结束的调用可能缺失，因此本功能用于运行观察，不能作为精确计费账本。
- SQLite 数据库应放在运行主机的本地文件系统/容器挂载目录；不要让多台主机通过 SMB/NFS 共写同一文件。运行中的计数仅覆盖当前 REST 进程，不是跨实例合计。

私有数据库目录及 SQLite/WAL/SHM 文件不进入 Git 或 Docker 构建上下文。在线备份应使用 SQLite 备份机制；停服备份时保留数据库相关文件，不要在写入期间只复制主文件。

## 查询接口

```http
GET /v1/stats?period=7d
Authorization: Bearer <服务密钥>
```

`period` 为 `today`、`7d` 或 `30d`，默认 `today`。返回字段：

| 字段 | 内容 |
|---|---|
| `summary` | `total`、`succeeded`、`failed`、`cancelled`、`success_rate`（0–1/null）、`average_seconds`、`p95_seconds`、`request_bytes`、`response_bytes`、`preparation_retries`、`reply_recoveries` |
| `daily` | 日期和当日成功/失败/取消数、请求/响应字节数；范围内无记录日期补零 |
| `phase_averages` | 按阶段名称返回平均秒数，未进入过的阶段为 `null` |
| `recent` | 最近 50 条，包含诊断请求 ID、开始时间、结果、HTTP 状态、耗时、流量、实际是否 SSE、结束阶段及提交状态；无法得到响应状态的断线记录使用 `http_status: 0` |
| `live` | 当前密钥的 `active` 和 `queued` |
| `capacity` | 配置的浏览器槽位数 |
| `storage` | `available`、`pending_records`、`dropped_records`、`write_errors`、`retention_days`；这些运行状态不跨重启累计 |
| `recording_since`、`generated_at` | 数据库首次建立时间、报告生成时间，Unix 秒 |

统计接口复用服务鉴权，设置 `Cache-Control: no-store`；401 表示鉴权失败，400 表示范围参数错误，503 表示统计存储不可用。503 不代表聊天服务不可用，不能因此重发聊天。静态 `/stats` 页面本身不含任何历史数据。

## English

Open `/stats` on the existing REST service. Authenticate with a service API key to view that key's history. “Remember for 7 days on this device” is enabled by default: after a successful statistics response, the key and a fixed expiry are saved in this origin's browser local storage. Reloads restore it without extending expiry. Expiry, logout or HTTP 401 clears it; other open remembered sessions observe logout. Uncheck the option for shared devices. Disabled storage falls back to page-memory login with a notice. Keys never enter URLs or the statistics database; this does not change the service key's validity.

The dashboard provides today/7-day/30-day views, automatic refresh, outcome and latency summaries, body-byte counts, live activity, daily trends, phase averages and the latest 50 attempts. Dates use UTC+8, and started time determines the bucket.

Only authenticated terminal REST chat attempts are recorded, including input failures and cancellation. Polls, health checks, unauthorized requests and MCP calls are excluded. SSE errors are failures even with HTTP 200. A gateway replacement is a separate HTTP attempt, not a deduplicated business task. Byte counts describe application bodies, not total network traffic, token consumption or billing.

History uses SQLite with 90-day default retention and a persistent Compose mount. A bounded queue and one worker thread keep disk I/O off the chat event loop. Storage failure is surfaced without failing chats. Sudden shutdown may lose queued records or unfinished attempts; this is operational telemetry, not a billing ledger. No messages, replies, images, conversation IDs or raw keys are stored. Existing history is not backfilled.

NAS deployment passed on 2026-09-26 with one live synthetic chat. Asset delivery, authentication, in-flight activity, exact body-byte accounting and the persisted record were verified. Progress/statistics polling did not increase the chat count; both browser workers were connected and idle afterward. Local preview fixtures were not imported.
