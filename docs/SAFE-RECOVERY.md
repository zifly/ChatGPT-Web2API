# 请求内安全恢复 / In-request safe recovery

[错误码](ERROR-CODES.md) · [Error codes](ERROR-CODES.en.md) · [浏览器暂停与恢复 / Browser recovery](REQUEST-RECOVERY.md)

## 中文

本说明描述单次 HTTP 请求内的安全准备恢复。关闭 SDK、任务队列和代理的原样自动重放；最终错误的 `automatic_retry_allowed` 仍是 `false`。2026-09-25 起，网关可在原请求最终失败后按 `new_conversation_retry` 放弃旧尝试、新建会话重试一次，详见[替换重试约定](NEW-CONVERSATION-RETRY.md)。两者的次数分别计算：本页计数只用于请求内只读准备，网关仍须执行整个业务任务最多一次的替换预算。

### 实际恢复范围

仅非流式 `stream:false` 且明确提供 `conversation_id` 的请求，允许在 `navigation_timeout` 后有限重新确认页面就绪。输入、上传和发送尚未开始，整个请求的发送检查点计数必须为零。新会话、隐式沿用会话和 SSE 不启用此准备恢复。

首次导航后不再次执行 `Page.navigate`。恢复只读取原页面，并继续持有原操作锁。要求标签、连接与目标会话身份不变，保护器未暂停、无已开启的熔断器、无不确定的传输操作，并通过空输入、无附件、无进行中生成的检查。缺少表单或无法证明条件成立时拒绝恢复。退避前后和恢复轮询期间重新检查条件。

不恢复 `navigation_failed`、`navigation_displaced`、输入失败、图片上传失败、未知 500 或发送结果不确定。图片请求也只能在上传开始前适用上述导航恢复，不能重传附件。不会换账号、模型、会话或标签，不自动调用管理员恢复接口，不重启浏览器。

已发送后沿用 backend 模式原有的同一会话/同一轮次读取：部分读取超时可在原截止时间内继续读取；保护器和取消仍生效。最终 `reply_timeout` 不会触发新一轮发送或延长截止时间。这不是所有读取失败都可恢复的承诺。

### 时间与配置

| 项目 | 当前值 / 行为 |
|---|---|
| 准备尝试上限 | 3 次，含首次；最多额外 2 次 |
| 追加尝试前退避 | 5 秒、15 秒，无抖动 |
| 每次导航确认预算 | 30 秒 |
| 整体请求预算 | 继续使用 `server.request_timeout`，默认 120 秒；不重置 |
| 下一次恢复准入 | 剩余时间须容纳退避 + 30 秒导航确认 + 30 秒回复预留；安全探针后再次检查 |
| 管理员可配置范围 | 继续支持现有整体 request_timeout；尝试数、退避和预留值目前是代码常量，没有新增环境变量或请求参数 |

30 秒回复预留只用于决定是否启动下一轮，不保证回复能在 30 秒内完成。默认总预算下，连续两轮完整导航超时后通常不会启动第三轮；这是正常的预算保护。预算不足时保留 `navigation_timeout`，诊断使用 `insufficient_budget`；真正整体到期由现有保护器返回 `request_timeout`。

### 限流与底层操作

REST 中的限流直接返回 429，不再重复运行包含输入/发送的工厂函数，也不自动消除限流提示。`Retry-After` 向上取整到秒；网关替换重试须等待该冷却并遵守独立的一次预算。非 REST 的旧限流帮助函数仍保留有限尝试，但要求等待超过其 cap 时直接返回错误，不将等待截短后重试。

带 REST 请求上下文的 CDP 操作不再在 socket 发送失败后自动重连并重放命令；导航恢复是唯一新增的准备恢复入口。超时或取消的 CDP 操作会留下本请求的“不确定传输”标记，阻止导航恢复，因为 Python 等待结束不能证明浏览器已结束原操作。

### 新增响应诊断

可解析的聊天 JSON 响应，包括正常成功、鉴权/参数错误及受保护运行错误，增加顶层 `request_diagnostics`。SSE 正常终止块和受保护错误事件也提供诊断，但不启用新的准备恢复。框架、代理、断连或旧版本响应仍可能没有这些字段。

| 字段 | 定义 |
|---|---|
| `request_id` | 服务端随机生成的 32 位十六进制追踪 ID，和服务日志关联；不是幂等键，无按 ID 查询能力 |
| `preparation_attempt_count` | 取得操作锁后进入首次准备计为 1；每次实际启动追加准备加 1；提前失败可为 0 |
| `retry_count` | 实际启动的追加准备次数；退避中取消不计数，最大 2 |
| `retry_codes` | 对应追加准备的错误码数组，最大 2 项；首期仅 `navigation_timeout` |
| `reply_recovery_attempt_count` | backend 读取遇到可捕获超时后，完成等待、通过保护检查并准备继续读取的次数；正常“尚未完成”轮询不计数 |
| `elapsed_seconds` | 单调时钟计算的处理器耗时；不包含调用方收到响应后的处理 |
| `terminal_reason` | 下表中的停止原因 |

| `terminal_reason` | 含义 |
|---|---|
| `succeeded` | API 处理正常完成，业务内容仍需调用方校验 |
| `attempts_exhausted` | 已完成允许的准备尝试次数仍未就绪 |
| `insufficient_budget` | 剩余时间不足以启动下一次恢复，可能尚未到整体截止时间 |
| `deadline_exceeded` | 整体截止时间耗尽 |
| `non_recoverable` | 不在白名单、缺少安全证据、提前校验失败或其他未恢复错误 |
| `send_outcome_unknown` | 发送结果不确定 |
| `client_disconnected` | 检测到客户端断开 |
| `cancelled` | 其他取消，通常只能在服务日志看到 |

失败优先级：断连 → 发送未知 → 整体到期 → 已记录的恢复停止原因 → non_recoverable。三态发送字段独立保留，不被停止原因替代。断连、强制停止或进程重启不保证有终态响应；服务不会在重启后自动重放在途请求。

同步非流式响应没有实时恢复进度，客户端只能等待最终结果。内部恢复成功不需要当作错误通知。诊断不包含正文、图片、凭据或真实会话 URL；原始异常和其他既有日志仍须审查后再共享。

验证范围：352 项相关离线测试通过；四项串行合成 NAS 测试通过，覆盖文字新聊、文字续聊、识图、图片续聊及诊断兼容性，每项客户端只提交一次 POST。恢复计数全部为 0，因此真实故障恢复分支仍未验证。此处仅记录匿名结论，不附带下游项目记录。

## English

This contract describes read-only preparation recovery inside one HTTP request. Ordinary client replay stays disabled and final errors retain `automatic_retry_allowed=false`. A gateway may separately abandon a failed attempt and retry once in a new conversation under the [replacement contract](NEW-CONVERSATION-RETRY.md). Preparation counters here do not reset or replace the gateway's task-wide one-replacement budget.

Only non-streaming requests with an explicit `conversation_id` can recover from `navigation_timeout`, before input, upload or any send attempt. New chats, implicit continuation and SSE do not use this preparation recovery. Image requests can benefit before uploading, but uploads themselves are never replayed.

Recovery never repeats `Page.navigate`. It holds the original mutation lock and only reads the original page. Target, socket and conversation identity must remain unchanged; the browser cannot be paused, blocked by a breaker, or carrying an uncertain transport operation. Empty composer, no attachments and no ongoing generation must be positively verified. Missing evidence rejects recovery. Checks run before/after backoff and during recovery polling.

`navigation_failed`, displacement, input/upload failures, unknown 500s and uncertain sends are not recovery candidates. No account/model/conversation/tab switching, browser restart or automatic administrator recovery occurs.

Backend reply reading retains its existing anchored polling: some read timeouts may resume reading the same turn within the original deadline and guard constraints. Final reply timeout never authorizes sending again or extending the deadline.

Limits are three preparation attempts including the first, backoffs of 5 and 15 seconds without jitter, and 30 seconds per navigation attempt. The existing overall `server.request_timeout` remains 120 seconds by default. A new recovery requires enough time for backoff + a complete navigation budget + a 30-second reply reserve, rechecked after safety probes. The reserve is an admission threshold, not a generation guarantee. Attempt/backoff/reserve values are code constants, not new environment variables or request options. Under the default total budget, two full navigation timeouts normally prevent a third attempt.

REST rate limits now propagate without replaying the input/send factory or automatically dismissing the popup. `Retry-After` is rounded up to seconds. The legacy non-REST helper rejects cooldowns above its cap instead of shortening them. REST CDP commands no longer reconnect/replay after socket-send failure; timed-out or cancelled commands prevent preparation recovery because remote completion is uncertain.

Chat JSON responses add top-level `request_diagnostics`, including success and early authentication/validation failures. SSE terminal/error events also carry diagnostics where request context exists, without enabling preparation recovery. Framework/proxy errors, disconnected clients and old versions can still lack these fields.

| Field | Meaning |
|---|---|
| `request_id` | Server-generated 32-character hexadecimal trace ID; not an idempotency key, with no result-query endpoint |
| `preparation_attempt_count` | One upon entering preparation after acquiring the lock, plus each actual recovery attempt; zero for earlier failures |
| `retry_count` | Additional preparation attempts actually started, at most two; cancellation during backoff does not increment it |
| `retry_codes` | Matching codes, at most two; initially only `navigation_timeout` |
| `reply_recovery_attempt_count` | Backend read-timeout recoveries that finish waiting and pass guard checks before resuming reads; ordinary not-ready polls are excluded |
| `elapsed_seconds` | Monotonic elapsed handler time |
| `terminal_reason` | One of the values below |

Reasons: `succeeded`, `attempts_exhausted`, `insufficient_budget`, `deadline_exceeded`, `non_recoverable`, `send_outcome_unknown`, `client_disconnected`, `cancelled`. Insufficient budget preserves the original navigation error even if the overall deadline has not expired. Failure precedence is disconnect, unknown submission, overall expiry, recorded recovery-stop reason, then non-recoverable. Submission state remains independent.

Disconnect, forced termination and restart cannot guarantee a final response; there is no automatic replay after restart. Non-streaming calls provide no live recovery progress. Successful internal recovery need not trigger an error notification. Business validation remains the client's responsibility. Diagnostic fields exclude prompts, images, credentials and conversation URLs; existing raw errors/logs still require review before sharing.

Validation: 352 related offline tests passed. Four sequential synthetic NAS calls passed for new text, text continuation, image recognition and image continuation, with valid diagnostics and one client POST each. All recovery counts were zero; recovery under a real webpage fault remains unverified. Only anonymized conclusions are included here, without downstream project records.

## Synthetic response examples / 合成响应示例

These examples contain no real conversation or request data. Example timings illustrate field meaning, not performance guarantees. 请求 ID 和会话 ID 均为合成示例。

### 200 — recovery succeeded / 内部恢复成功

```json
{
  "id": "chatcmpl-synthetic", "object": "chat.completion", "created": 0,
  "model": "auto", "conversation_id": "synthetic-conversation",
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "Synthetic complete answer."}, "finish_reason": "stop"}],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
  "request_diagnostics": {"request_id": "00000000000000000000000000000001", "preparation_attempt_count": 2, "retry_count": 1, "retry_codes": ["navigation_timeout"], "reply_recovery_attempt_count": 0, "elapsed_seconds": 58.2, "terminal_reason": "succeeded"}
}
```

### 504 — preparation stopped / 准备恢复停止

Default-budget example. With a larger configured budget, three failed attempts can instead yield `attempts_exhausted`, count 3, retry count 2 and two retry codes.

```json
{
  "error": {
    "code": "navigation_timeout", "type": "server_error",
    "message": "Conversation navigation failed: navigation_timeout; stage=composer_unavailable",
    "phase": "navigation", "send_state": "not_sent", "prompt_sent": false, "automatic_retry_allowed": false,
    "navigation": {"stage": "composer_unavailable", "url_matches": true, "ready_state": "interactive", "app_shell_present": true, "composer_present": true, "composer_usable": false, "probe_error": null, "elapsed_ms": 30000}
  },
  "request_diagnostics": {"request_id": "00000000000000000000000000000002", "preparation_attempt_count": 2, "retry_count": 1, "retry_codes": ["navigation_timeout"], "reply_recovery_attempt_count": 0, "elapsed_seconds": 65.2, "terminal_reason": "insufficient_budget"}
}
```

### 504 — sent, reply collection failed / 已发送但读取失败

```json
{
  "error": {"code": "request_timeout", "type": "server_error", "message": "Request deadline exceeded", "phase": "reply", "send_state": "confirmed", "prompt_sent": true, "automatic_retry_allowed": false},
  "request_diagnostics": {"request_id": "00000000000000000000000000000003", "preparation_attempt_count": 1, "retry_count": 0, "retry_codes": [], "reply_recovery_attempt_count": 1, "elapsed_seconds": 120.0, "terminal_reason": "deadline_exceeded"}
}
```

### 504 — submission uncertain / 发送结果未知

```json
{
  "error": {"code": "browser_timeout", "type": "server_error", "message": "CDP timeout: Runtime.evaluate", "phase": "send", "send_state": "unknown", "prompt_sent": null, "automatic_retry_allowed": false},
  "request_diagnostics": {"request_id": "00000000000000000000000000000004", "preparation_attempt_count": 1, "retry_count": 0, "retry_codes": [], "reply_recovery_attempt_count": 0, "elapsed_seconds": 20.0, "terminal_reason": "send_outcome_unknown"}
}
```
