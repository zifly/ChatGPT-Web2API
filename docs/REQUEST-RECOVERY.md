# 请求超时与浏览器恢复 / Request deadlines and browser recovery

[中文 API 手册](../API使用与项目接入手册.md) · [English API guide](../API-USAGE.md)

## 中文

REST 池模式见[多会话并发接入](REST-CONCURRENCY.md)。允许网关放弃旧尝试、[在新会话重试一次](NEW-CONVERSATION-RETRY.md)，包括发送状态不确定的情况；服务端总体截止时间和发送三态不变。

REST 聊天请求从进入处理器起共用服务端 `request_timeout`（默认 120 秒），包括读取请求、排队、导航、输入、上传和回复等待。客户端超时应大于服务端预算并留出传输余量，例如默认配置下使用 180 秒。预算耗尽后停止后续操作；检测到客户端连接关闭时也停止该请求。取消无法撤回已经交给浏览器的命令，所以点击发送后没有收到确认时，不能认定为“没发出去”。错误事件发送和锁释放可能带来少量收尾时间。

响应的 `error` 增加以下字段：

| 字段 | 含义 |
|---|---|
| `phase` | 失败阶段，如 `queue`、`navigation`、`input`、`upload`、`send_ready`、`send`、`reply` |
| `send_state=not_sent` / `prompt_sent=false` | 本次请求尚未尝试发送 |
| `send_state=confirmed` / `prompt_sent=true` | 已观察到本轮提交或完整回复的确认 |
| `send_state=unknown` / `prompt_sent=null` | 已尝试点击发送但未获得确认；允许放弃旧尝试后新建重试，不能继续向旧会话补发 |
| `automatic_retry_allowed=false` | 普通 HTTP/SDK 不得原样重放旧请求 |
| `new_conversation_retry` | 网关替换重试策略：是否允许、最多一次、等待秒数；网关负责预算和旧结果隔离 |

`504 request_timeout` 是整体截止时间耗尽；`504 browser_timeout` 是单次浏览器命令超时；`503 browser_unresponsive` 表示浏览器操作已暂停。已有的上传、回复和限流错误代码继续保留，但应以新的三态发送字段判断本次状态，不能只看 HTTP 状态码。已开始的 SSE 不能更换 HTTP 状态：读取顶层 `error` 和 `choices[0].finish_reason=error`，不能把 HTTP 200 当作生成成功。

连续两次 CDP 命令超时会暂停后续 REST 浏览器操作；发送状态不确定的失败，或持有浏览器操作锁时整次请求被中断，也会暂停。单路模式暂停期间新聊天请求返回 503；池模式暂停对应槽位并保留会话绑定，其他健康槽位仍可服务其他会话；排队超时本身不会把正在处理其他请求的浏览器判为故障。`/health` 会显示 `degraded`，并提供 `browser_paused`、`browser_pause_reason`、`consecutive_cdp_timeouts` 和 `last_error_at`。最后一次错误是历史记录，成功的聊天请求会清除它。池模式下 `/health.rest_pool.slots` 提供各槽位状态，顶层 `browser_paused` 表示全部槽位暂停。恢复可指定 `POST /v1/browser/recover?slot=0`，不会打断忙碌槽位或重放消息。此暂停机制针对当前 REST 服务进程，不是多个独立进程之间的全局熔断。

若希望继续核对旧结果，可先查看原网页。若选择放弃旧尝试并重试，则先更新业务任务的尝试版本；不必等旧结果核对完成。其他健康槽位可直接服务新会话。需要恢复故障槽位时，发送带正常 API Key 的空 POST，池模式可指定 `?slot=N`：

```text
POST /v1/browser/recover
Authorization: Bearer <自己的服务密钥>
```

该接口在同一操作锁下做只读脚本响应检查，最多等待约 5 秒；成功后解除暂停。若未获得操作锁，返回 `503 browser_busy`，不会中断当前任务。它不点击、不导航、不重连或重启浏览器，也不会重发之前的请求。检查成功不等于此前的不确定消息已经核对，更不保证登录有效或下一次请求一定成功。如果检查失败，先排查页面和 NAS 资源，必要时等待业务任务停止后人工重启服务，再做一次新的通用测试。

本次离线故障注入覆盖导航/排队截止时间、客户端断开、点击结果不确定、JSON/SSE 错误、CDP 暂停与只读恢复、图片取消后不再操作页面、发送后不重试限流。真实 NAS 故障注入验收尚待完成；这不说明网页卡顿的资源或网络根因已经解决。

## English

In pooled REST, guards and browser failures are isolated per worker. Paused conversations remain bound to that slot; other healthy slots can serve other conversations. `/health.rest_pool.slots` shows per-worker state, and the top-level `browser_paused` means all slots are paused. The recovery endpoint accepts `?slot=0`; without it, paused workers are probed (all workers if none are paused). Busy workers are never interrupted. Account cooldown is shared across the pool and is not cleared by recovery. See [concurrent REST migration](REST-CONCURRENCY.md).

REST chat uses one `request_timeout` budget (120 seconds by default) from handler entry, covering body reading, queueing, navigation, typing, upload and reply waiting. Give clients a larger timeout, such as 180 seconds for the default server budget. Deadline expiry or a detected client disconnect stops subsequent work. Cancellation cannot retract commands already submitted to Chrome; a click without acknowledgment has an unknown outcome. Error-event delivery and lock release may add a small cleanup interval.

Errors include `phase`, `send_state` (`not_sent`, `confirmed`, `unknown`), `prompt_sent` (`false`, `true`, `null`) and `automatic_retry_allowed=false` for ordinary request replay. The additive `new_conversation_retry` policy permits a gateway to abandon even an uncertain/confirmed attempt and replace it once in a new chat, rebuilding inputs and discarding late results. See [replacement retry requirements](NEW-CONVERSATION-RETRY.md). `504 request_timeout` means the overall budget expired, `504 browser_timeout` a single command timed out, and `503 browser_unresponsive` that operations are paused. After SSE headers, inspect the top-level `error` and `finish_reason=error`; HTTP 200 alone is not success.

Two consecutive CDP command timeouts pause subsequent REST operations. An unknown-send failure or interruption while holding the browser mutation lock also pauses them. Queue expiry alone does not pause another active turn. Health reports `degraded` plus the pause flag/reason, consecutive timeout count and historical error timestamp. Successful chat clears the historical error. This guard belongs to one REST process, not a global breaker across independent MCP servers.

To retain the old attempt, reconcile it in the webpage. To abandon it, invalidate its result version before retrying; another healthy worker can serve the replacement. An authenticated empty `POST /v1/browser/recover`, optionally with `?slot=N`, performs a read-only probe within about five seconds. Busy workers return `503 browser_busy`. Success unpauses the worker without navigating, reconnecting, restarting, resending or clearing account cooldown. Do not restart the shared service as part of an individual task retry, because other conversations may be active.

Offline fault injection covers deadlines, disconnection, uncertain clicks, JSON/SSE errors, pause/recovery, cancelled image cleanup and no rate-limit replay after submission. Live NAS fault-injection acceptance is pending; the underlying cause of a slow browser has not been established.
