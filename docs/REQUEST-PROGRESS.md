# 请求进度与阶段耗时 / Request progress and timings

更新：2026-09-26。[API 接入](../API使用与项目接入手册.md) · [并发会话](REST-CONCURRENCY.md) · [替换重试](NEW-CONVERSATION-RETRY.md)

项目端可以在聊天尚未返回时显示“等待可用浏览器”“打开会话”“上传图片”“等待并核对回复”，并显示已经等待的秒数。此更新提供可观察的阶段信息，不会加快模型生成，也不估算完成百分比或剩余生成时间。

## 1. 调用方式

现有 JSON 和 SSE 请求都可以使用，`stream` 不需要为了进度查询而改变。未提供 `progress_id` 的旧调用保持原有行为，仅最终 `request_diagnostics` 增加阶段耗时。

1. 可先检查 `GET /health` 中的 `request_progress.supported`。缺少该字段的旧版本暂不支持实时进度，项目端退回普通等待提示。
2. 网关为**本次尝试**生成一个新的 UUID，作为聊天请求顶层的 `progress_id`。不要在发送前等待进度记录出现。
3. 发起原聊天 POST，同时每 **1 秒**调用一次 `GET /v1/requests/{progress_id}`，使用与聊天 POST **相同的 API Key**。该查询不占浏览器槽位，也不发送聊天。
4. 原 POST 返回、失败或被取消后，停止轮询。最终回答、会话 ID 和错误以原 POST 的 JSON/SSE 结果为准；进度接口不返回这些内容。

聊天请求示例，示例 UUID 每次使用时需重新生成：

```json
{
  "model": "auto",
  "stream": false,
  "new_conversation": true,
  "progress_id": "550e8400-e29b-41d4-a716-446655440000",
  "messages": [{"role": "user", "content": "Reply with exactly: OK"}]
}
```

等待该 POST 返回期间，另一个 HTTP 请求查询：

```http
GET /v1/requests/550e8400-e29b-41d4-a716-446655440000
Authorization: Bearer <与聊天请求相同的服务密钥>
```

运行中的响应示例，耗时仅为示意：

```json
{
  "object": "chat.request.progress",
  "progress_id": "550e8400-e29b-41d4-a716-446655440000",
  "request_id": "00000000000000000000000000000001",
  "status": "running",
  "phase": "navigation",
  "elapsed_seconds": 20.0,
  "phase_elapsed_seconds": 19.7,
  "phase_timings": {"validation": 0.1, "queue": 0.2, "navigation": 19.7},
  "remaining_seconds": 100.0,
  "send_state": "not_sent",
  "preparation_attempt_count": 1,
  "retry_count": 0,
  "retry_codes": [],
  "reply_recovery_attempt_count": 0,
  "terminal_reason": null
}
```

OpenAI Python SDK 可以通过 `extra_body={"progress_id": progress_id}` 传入该字段；使用独立的并行 HTTP 请求轮询，SDK 的普通重试仍保持 `max_retries=0`。`progress_id` 不是 `conversation_id`，不能用来续聊；服务端生成的 `request_id` 仍用于诊断关联。

## 2. 项目端显示什么

| `phase` | 建议文案 |
|---|---|
| `validation` | 正在检查请求 |
| `queue` | 等待可用浏览器或会话操作锁 |
| `prepare` | 正在准备网页会话 |
| `model_selection` | 正在切换模型 |
| `navigation` | 正在打开会话 |
| `input` | 正在输入问题 |
| `upload` | 正在上传并确认图片 |
| `send_ready` | 等待网页允许发送 |
| `send` | 正在发送并确认提交 |
| `reply` | 等待并核对完整回复 |

`reply` 包含网页生成、读取和结果核对；服务无法观察模型内部的“思考百分比”。推荐显示“正在打开会话 · 已等待 20 秒”，不要把阶段换算成虚构进度条。

`status` 独立于阶段，可为 `running`、`succeeded`、`failed`、`cancelled`。结束时 `phase` 保留最后执行的阶段，耗时冻结；例如 `failed + reply` 表示在回复阶段结束。`remaining_seconds` 是服务端请求预算剩余秒数，**不是预计多久回答完**；终态为 0。`phase_elapsed_seconds` 是本次连续处于该阶段的时间；`phase_timings` 累计每个阶段的时间，包含多次进入同一阶段的情况。

最终 JSON 响应以及 SSE 正常结束块、受保护错误事件中的 `request_diagnostics` 也包含 `phase`、`phase_elapsed_seconds`、`phase_timings`；启用轮询时还包含 `progress_id`。项目端可以据此区分页面打开慢、排队慢和回复等待慢。不必把详细诊断字段全部展示给最终用户。

## 3. 网关与前端如何配合

网关保存业务会话、当前 `attempt_id` 和 `progress_id` 的对应关系，使用服务密钥查询后，再通过项目自己的轮询、SSE 或 WebSocket 将必要状态给前端。复用现有网关鉴权，不要为了显示进度把后端服务密钥下发到公开页面。

建议处理流程：

```text
生成新的 attempt_id 和 progress_id
立即发起聊天 POST
并行执行：每秒查询进度
  如果仍是当前尝试，更新这条业务消息的阶段和等待时长
  查询不可用时，只显示“暂时无法获取进度”，继续等待原 POST
原 POST 结束：停止轮询，按 JSON/SSE 成功或失败处理
```

- 只修改发起请求的业务消息，切换面板不能让 A 的进度写到 B。
- 放弃旧尝试并新建重试时，生成**新的 `progress_id`**，停止旧轮询，并对已在途的旧轮询结果做 `attempt_id` 校验。相同的校验也适用于最终结果和会话 ID 回填。
- 进度查询失败不代表聊天失败，不能因此补发 POST、换会话或重启服务。查询本身没有重试聊天的副作用。
- `succeeded` 只说明服务端处理结束，不能替代原请求的完整回答；SSE 仍须检查顶层 `error`、正常 `stop` 和 `[DONE]`。
- 用户取消时关闭原 POST/SSE 并停止轮询；只停止轮询不会取消聊天。已经交给网页的生成不保证被撤回。

## 4. 权限、保留时间与错误

进度按服务 API Key 隔离：另一个有效 Key 查询已知 UUID 也返回 404。多个业务用户共享一个服务 Key 时，业务用户之间的权限仍由网关负责。未配置 API Key 的部署没有用户隔离，所有匿名请求共用命名空间。响应不含提示词、图片、回答、真实会话 ID 或密钥，且设置 `Cache-Control: no-store`。

状态保存在**单个 REST 进程内存**，最多 256 条，完成后保留 300 秒；活动请求不会因该保留时间被提前清理。进程重启后记录消失。多实例部署必须将聊天和对应进度查询路由到同一进程；这与两路浏览器槽位不同，槽位共享同一份进度存储。

| 返回 | 处理 |
|---|---|
| `400 invalid_progress_id` | `progress_id` 必须是 UUID 字符串，接受 32 位十六进制或标准带连字符形式；不会开始浏览器操作 |
| `401` | 检查服务鉴权；不能自动重新发聊天 |
| `404 progress_not_found` | POST 尚未登记、已过期/重启或 Key 不同；先等待原 POST，不凭此推断任务失败 |
| `409 progress_id_conflict` | 同 Key 的 ID 已登记，包含仍保留的终态；重复 POST 不执行浏览器操作，也不覆盖原状态 |
| `503 progress_capacity_exceeded` | 进度存储已满，请求未进入浏览器；等待完成记录过期，不循环补发 |

记录是在请求鉴权和输入校验后登记，因此抢先轮询可能短暂得到 404，校验失败的请求不会有进度记录。ID 过期后可以重新登记，所以它**不是持久化幂等键**；始终每次尝试生成新 ID，网关继续执行任务级一次替换重试预算。

## English

Optionally include a fresh UUID as top-level `progress_id` in each chat attempt, for either JSON or SSE. Start the POST immediately and poll `GET /v1/requests/{progress_id}` concurrently about once per second with the same API key. `/health.request_progress.supported` advertises availability. Existing chat wire formats remain; SSE does not gain custom progress chunks.

The snapshot reports `status`, `phase`, elapsed/phase timings, remaining server deadline budget and submission state. It contains no prompts, attachments, replies, conversation IDs or credentials. Status is `running`, `succeeded`, `failed` or `cancelled`; final timings freeze. The original POST remains authoritative for the actual result and conversation ID. Final `request_diagnostics` also includes phase timings, even without polling.

Use gateway authorization and per-attempt checks before forwarding progress to the frontend. Stop polling when the POST ends or the user cancels. A replacement attempt needs a new UUID; ignore late progress from abandoned attempts. Poll failures must never resend the chat. An initial 404 may be a registration race, and records also disappear after restart or expiry.

Records are scoped by service API key, held in one REST process, bounded to 256 entries and retained for 300 seconds after completion. Anonymous deployments share one namespace; users sharing a service key require gateway-level isolation. Keep multi-instance routing consistent. Duplicate registered IDs return 409 before browser work, invalid IDs return 400, and exhausted progress capacity returns 503. This is progress reporting, not durable idempotency, an ETA or a claim of faster generation.
