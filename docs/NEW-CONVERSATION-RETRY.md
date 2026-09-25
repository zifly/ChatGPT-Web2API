# 放弃旧会话后重试 / Retry in a new conversation

更新：2026-09-25。[并发接入](REST-CONCURRENCY.md) · [错误与恢复](REQUEST-RECOVERY.md)

允许网关在超时、短暂连接故障、HTTP 5xx 或限流冷却结束后，**放弃旧尝试，在全新会话自动重试一次**。包括原请求 `send_state=unknown` 或 `confirmed` 的情况。放弃表示不再接收旧结果、不再续用旧 ID；不要求先确认旧网页有没有生成完，也不删除账号中的聊天记录。

旧网页请求可能仍会完成并消耗额度。网关按本约定实现结果过滤后，业务只采用当前尝试的结果；web2api 的提示字段本身不会替调用方过滤旧结果，也不能保证模型只执行一次。重复生成可以接受时，按本约定重试即可。

## 1. 错误响应约定

JSON 错误和 SSE 顶层 `error` 均提供以下提示：

```json
{
  "error": {
    "send_state": "unknown",
    "automatic_retry_allowed": false,
    "new_conversation_retry": {
      "allowed": true,
      "mode": "new_conversation",
      "max_retries": 1,
      "retry_after_seconds": 2
    }
  }
}
```

- `automatic_retry_allowed=false` 保留兼容语义：普通 HTTP/SDK 不得原样重放请求，尤其不能继续向旧 ID 补发。
- `new_conversation_retry.allowed=true` 明确允许网关执行本页的新会话替换重试。429、5xx 返回 true；参数、鉴权等其他 4xx 返回 false。明确的客户端取消不触发重试。
- 允许时 `max_retries=1`，不允许时为 `0`。一次上限作用于**整个业务任务**，由网关持久化计数并执行；web2api 无法把多个独立 HTTP 请求自动归为同一个任务，不在内部再重试发送。第二次请求即使再次返回 `allowed=true`，也不能重置任务预算。
- `retry_after_seconds` 通常为 2 秒；429 时至少等待 `Retry-After` 指定的时长。SSE 错误把等待时长放在该字段，因为 HTTP 头可能已经发出。

如果网络超时/连接断开导致未收到完整错误体，网关仍可按本页策略新建重试一次；须区分网络故障与用户主动取消。旧版服务没有此提示字段时，可通过网关自己的显式策略启用本约定，不能让普通 SDK 猜测重试方式。400/401/403 先修复输入或登录，不自动换会话碰运气。

| 情况 | 网关处理 |
|---|---|
| 5xx，且不是明确的客户端取消 | 按提示退避，预算尚未使用且能重建输入时，新建重试一次 |
| 429 | 至少等待指定冷却，再使用同一份任务重试预算；换会话不能绕过账号限流 |
| 网络故障，没有完整错误体 | 按网关显式策略执行同样的一次替换流程 |
| 其他 4xx、用户取消、预算已用尽或缺少必要上下文 | 结束自动重试，返回错误或提示补充输入 |

SSE 的 HTTP 200 和 `[DONE]` 都不能单独证明成功。先检查事件顶层 `error` 和 `finish_reason=error`；成功结果必须收到正常 `stop` 和完整结束标记。错误发生在响应头发出后时，冷却信息从 `error.new_conversation_retry.retry_after_seconds` 读取。

## 2. 网关按这个顺序处理

1. 原尝试失败后，原子地占用一次重试预算，生成新的 `attempt_id`，将旧尝试标记为 abandoned，清除这个业务任务的旧 `conversation_id`。必须在等待恢复和重新发送之前完成。
2. 取消本地旧请求或关闭旧 SSE 读取，丢弃其局部输出。所有迟到的成功、失败、SSE 和会话 ID 回填，都必须带 `attempt_id`，仅当它仍等于任务的当前值时才允许写入。比较和写入应在同一事务/原子操作中执行，避免检查后又发生切换。
3. 等待退避或账号冷却。池中有健康槽位即可发送新会话；无需解除旧槽位的暂停。若没有可用槽位，可调用带鉴权的 `POST /v1/browser/recover?slot=N` 做只读探测。繁忙或探测失败时结束本次自动恢复，不循环重试。
4. 从业务侧已保存的数据重建完整输入，发送 `new_conversation: true`，**删除旧 `conversation_id` 字段**，保留所需模型、项目、系统提示和附件。使用一个全新的 HTTP 请求，并保持 SDK `max_retries=0`。
5. 收到完整成功结果后，再次原子校验 `attempt_id`，保存新 ID 与结果；同一业务会话今后使用新 ID。第二次仍失败时结束自动重试并展示错误。

不要为一个失败请求自动重启整个共享服务；这会打断其他正在工作的会话。账号限流不会因为新会话或恢复探测而消失。对旧 ID 的服务端暂停保护继续保留，恢复接口也不会重新发送旧任务。

前端若只调用业务网关，重试预算、重建输入和真实会话 ID 映射可统一放在网关。前端只显示最终有效尝试的状态；重新尝试期间不要把旧事件写回当前面板。正常续聊的响应 ID 必须不变，**经过网关明确发起的新会话替换后则应保存新 ID**；不要把这次有意替换误判为串会话。

## 3. 上下文和图片必须由业务侧补齐

- 独立任务：重发原任务的完整输入即可。
- 续聊任务：不能只发送“继续”“再分析一下”等增量文字。新会话没有旧上下文，网关需带上必要历史或任务摘要，以及本轮问题。服务当前只拼接有限历史，长任务应先整理所需上下文。
- 图片任务：新会话看不到旧会话附件，必须从业务存储重新附上必要图片。本接口只允许最后一条 user 消息包含图片，仍受最多 4 张、总计 6 MiB 等限制；不要直接重放带历史图片的整段 messages。
- 如果输入、图片或必要历史已无法重建，就结束自动重试并提示补充输入，不能静默改成缺少上下文的新任务。

重试请求示意：

```json
{
  "model": "auto",
  "new_conversation": true,
  "stream": false,
  "messages": [
    {"role": "system", "content": "本任务需要的系统要求"},
    {"role": "user", "content": "完整任务材料、必要上下文和本轮要求"}
  ]
}
```

`attempt_id`、abandoned 状态和业务重试计数属于网关的数据模型，不是新增的 web2api 请求字段。web2api 的 `request_diagnostics.request_id` 可用于排障，但不替代网关跨请求的任务版本控制。

## 4. 验收

模拟原请求超时且旧结果稍后到达：只显示新尝试结果，旧输出不能覆盖新 ID。覆盖首次新建失败、已有会话失败、图片重传、JSON/SSE、第二次失败停止、429 等待、用户取消不重试，并确认其他业务会话不被重启或取消。

web2api 本次通过 235 项相关离线测试，其中新增 14 项覆盖重试提示、失败后的独立新请求、旧槽位隔离、图片输入重建以及 JSON/SSE 冷却提示。浏览器使用合成替身；网关的预算持久化、旧事件过滤和前端回填仍需调用项目按上面的清单验收。

2026-09-25 已将重试提示更新部署到 NAS，本次仅更新应用代码，保留原有浏览器和系统依赖。部署后实际 HTTP 检查确认 400/401 返回禁止重试的策略；健康检查为两路已连接、空闲且未暂停。这组部署检查没有发送聊天请求，也未执行真实网关的超时替换重试。此前四条 NAS 合成聊天验证的是多会话并发，记录见[并发验收](REST-CONCURRENCY.md#7-本次-nas-部署与实测2026-09-25)。

## English

A gateway may abandon a failed attempt and retry once in a **new conversation**, including when the old submission was confirmed or uncertain. Ignore all late results from the abandoned attempt. This accepts duplicate model execution; it does not promise exactly-once generation or cancel the old server-side work.

Keep ordinary HTTP/SDK replay disabled. The additive `error.new_conversation_retry` policy permits one replacement after transient failures, with a cooldown included in JSON and SSE errors. The gateway enforces the total task budget, rebuilds context and attachments, removes the old ID and sends `new_conversation: true`. Use atomic attempt-version checks before accepting any result. Do not restart the shared service or interrupt unrelated conversations. Authentication/validation failures and explicit user cancellations are not automatic retries.

A second failure must end automatic recovery even if its policy again advertises `allowed=true`. Read top-level SSE errors and `finish_reason=error`; HTTP 200 or `[DONE]` alone is not success. Store the replacement conversation ID only after accepting the current attempt's complete success. The deployed service passed HTTP 400/401 policy and two-worker health checks without sending chat messages; end-to-end gateway replacement acceptance remains outstanding.
