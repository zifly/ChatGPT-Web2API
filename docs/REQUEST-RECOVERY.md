# 请求超时与浏览器恢复 / Request deadlines and browser recovery

[中文 API 手册](../API使用与项目接入手册.md) · [English API guide](../API-USAGE.md)

完整错误码参考 / Full error reference: [中文](ERROR-CODES.md) · [English](ERROR-CODES.en.md)。提前返回的鉴权/参数错误与恢复接口不包含完整聊天错误字段；详见参考手册。 Early authentication/validation and recovery responses do not contain the complete chat error fields; see the reference.

## 中文

新增的[请求内安全准备恢复](SAFE-RECOVERY.md#中文)只在明确会话的非流式请求中进行有限只读确认；与下面的管理员解除暂停接口不同，不自动解除保护或重发聊天。

REST 聊天请求从进入处理器起共用服务端 `request_timeout`（默认 120 秒），包括读取请求、排队、导航、输入、上传和回复等待。客户端超时应大于服务端预算并留出传输余量，例如默认配置下使用 180 秒。预算耗尽后停止后续操作；检测到客户端连接关闭时也停止该请求。取消无法撤回已经交给浏览器的命令，所以点击发送后没有收到确认时，不能认定为“没发出去”。错误事件发送和锁释放可能带来少量收尾时间。

响应的 `error` 增加以下字段：

| 字段 | 含义 |
|---|---|
| `phase` | 失败阶段，如 `queue`、`navigation`、`input`、`upload`、`send_ready`、`send`、`reply` |
| `send_state=not_sent` / `prompt_sent=false` | 本次请求尚未尝试发送 |
| `send_state=confirmed` / `prompt_sent=true` | 已观察到本轮提交或完整回复的确认 |
| `send_state=unknown` / `prompt_sent=null` | 已尝试点击发送，但未获得确认；先检查网页会话，禁止直接自动补发 |
| `automatic_retry_allowed=false` | 本接口不授权客户端自动重试该错误 |

`504 request_timeout` 是整体截止时间耗尽；`504 browser_timeout` 是单次浏览器命令超时；`503 browser_unresponsive` 表示浏览器操作已暂停。已有的上传、回复和限流错误代码继续保留，但应以新的三态发送字段判断本次状态，不能只看 HTTP 状态码。已开始的 SSE 不能更换 HTTP 状态：读取顶层 `error` 和 `choices[0].finish_reason=error`，不能把 HTTP 200 当作生成成功。

指定会话的导航另有最多 30 秒预算，包含页面导航和就绪检查，仍受整次请求剩余时间约束。同一会话的缓存 ID 与真实页面一致时复用该页面；连续两次检查均需确认目标 URL、应用界面及可见可编辑输入框。`interactive` 状态只有在这些条件也满足时才可继续，不再单独等待整个页面到达 `complete`。

导航预算耗尽返回 `504 navigation_timeout`；确认页面被跳转或导航命令失败分别返回 `502 navigation_displaced`、`502 navigation_failed`。聊天导航发生在输入和发送前，仍返回 `send_state=not_sent`、`prompt_sent=false`、`automatic_retry_allowed=false`。如果先耗尽整次预算，仍是 `request_timeout`。新增 `error.navigation` 仅包含失败阶段、URL 是否匹配、文档状态、应用/输入框就绪布尔值、探针错误分类和耗时，不包含真实 URL、会话 ID 或正文。不会自动重发导航命令或聊天，也不会切到另一个会话兜底。

连续两次 CDP 命令超时会暂停后续 REST 浏览器操作；发送状态不确定的失败，或持有浏览器操作锁时整次请求被中断，也会暂停。暂停期间新聊天请求返回 503；排队超时本身不会把正在处理其他请求的浏览器判为故障。`/health` 会显示 `degraded`，并提供 `browser_paused`、`browser_pause_reason`、`consecutive_cdp_timeouts` 和 `last_error_at`。最后一次错误是历史记录，成功的聊天请求会清除它。此暂停机制针对当前 REST 服务进程，不是多个独立 MCP 进程之间的全局熔断。

恢复步骤：先暂停调用方批量任务，检查网页是否已有未确认请求的消息或回复。然后发送带正常 API Key 的空 POST：

```text
POST /v1/browser/recover
Authorization: Bearer <自己的服务密钥>
```

该接口在同一操作锁下做只读脚本响应检查，最多等待约 5 秒；成功后解除暂停。若未获得操作锁，返回 `503 browser_busy`，不会中断当前任务。它不点击、不导航、不重连或重启浏览器，也不会重发之前的请求。检查成功不等于此前的不确定消息已经核对，更不保证登录有效或下一次请求一定成功。如果检查失败，先排查页面和 NAS 资源，必要时等待业务任务停止后人工重启服务，再做一次新的通用测试。

当前 352 项相关离线测试覆盖导航/排队截止时间、客户端断开、点击结果不确定、JSON/SSE 错误、CDP 暂停与只读恢复、图片取消后不再操作页面及 REST 限流不重放。四项串行合成 NAS 聊天通过，验证正常新聊、文字续聊、识图和图片续聊及新增诊断兼容性；恢复次数均为 0，真实故障下的恢复分支仍未触发，也不能据此断言网页卡顿根因已经解决。

## English

[In-request preparation recovery](SAFE-RECOVERY.md#english) performs bounded read-only checks for explicit-conversation non-streaming requests. It is separate from administrator unpausing below and never clears protections or replays a chat automatically.

REST chat uses one `request_timeout` budget (120 seconds by default) from handler entry, covering body reading, queueing, navigation, typing, upload and reply waiting. Give clients a larger timeout, such as 180 seconds for the default server budget. Deadline expiry or a detected client disconnect stops subsequent work. Cancellation cannot retract commands already submitted to Chrome; a click without acknowledgment has an unknown outcome. Error-event delivery and lock release may add a small cleanup interval.

Errors include `phase`, `send_state` (`not_sent`, `confirmed`, `unknown`), `prompt_sent` (`false`, `true`, `null`) and `automatic_retry_allowed=false`. Do not automatically retry unknown submissions. `504 request_timeout` means the overall budget expired, `504 browser_timeout` a single browser command timed out, and `503 browser_unresponsive` that browser operations are paused. Existing error codes remain, but use the three-state submission fields rather than assuming a status code proves whether a prompt was sent. After SSE headers, errors use a top-level `error` object and `finish_reason=error`; HTTP 200 alone is not success.

Explicit conversation navigation has a separate maximum 30-second budget covering navigation and readiness checks, still bounded by the remaining request time. A matching cached identity and live route can reuse the page. Two consecutive probes must verify the destination, app shell and visible editable composer. An `interactive` document is accepted only with those checks, rather than waiting solely for `complete`.

Navigation expiry returns `504 navigation_timeout`; confirmed route displacement or a rejected navigation command returns `502 navigation_displaced` or `502 navigation_failed`. Chat navigation precedes input and submission, so these errors retain `send_state=not_sent`, `prompt_sent=false` and `automatic_retry_allowed=false`. If the overall budget expires first, the code remains `request_timeout`. `error.navigation` contains only the stage, URL-match flag, document state, app/composer readiness flags, probe-error category and elapsed time, without actual URLs, conversation IDs or text. Navigation commands and chats are not automatically replayed, and failure does not open a replacement conversation.

Two consecutive CDP command timeouts pause subsequent REST operations. An unknown-send failure or interruption while holding the browser mutation lock also pauses them. Queue expiry alone does not pause another active turn. Health reports `degraded` plus the pause flag/reason, consecutive timeout count and historical error timestamp. Successful chat clears the historical error. This guard belongs to one REST process, not a global breaker across independent MCP servers.

Pause client batch work and reconcile uncertain submissions in the webpage first. An authenticated empty `POST /v1/browser/recover` acquires the same lock and performs a read-only script probe within about five seconds. A lock acquisition failure returns `503 browser_busy` without interrupting active work. Success unpauses the service; it does not navigate, reconnect, restart, resend, verify old submissions or guarantee account login. If it fails, inspect the webpage and host resources; a manual restart may be needed after business activity stops.

The current 352 related offline tests cover deadlines, disconnection, uncertain clicks, JSON/SSE errors, pause/recovery, cancelled image cleanup and no REST rate-limit replay. Four sequential synthetic NAS chats passed for new text, explicit-ID text continuation, image recognition and image continuation with valid diagnostics. All recovery counts were zero, so live fault recovery remains unverified; the underlying cause of a slow browser has not been established.
