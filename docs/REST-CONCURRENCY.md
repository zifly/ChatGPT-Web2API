# REST 多会话并发：前端与业务后端接入说明

更新：2026-09-25。本文对应新增的 REST 浏览器池实现。是否已在目标服务启用，以 `GET /health` 的 `rest_pool.enabled`、`rest_pool.size` 为准；仅更新源码不会更新正在运行的容器。

[中文调用手册](../API使用与项目接入手册.md) · [会话接入与 Python 示例](CONVERSATION-API.md) · [错误与恢复](REQUEST-RECOVERY.md) · [放弃旧会话后重试](NEW-CONVERSATION-RETRY.md)

## 1. 这次改动与兼容范围

接口地址、鉴权、`messages`、模型、图片格式、成功 JSON 和 SSE 格式保持兼容。`POST /v1/chat/completions` 和 `/chat/completions` 都可用。无需新增客户端会话字段。

NAS Compose 配置为 **2 路处理、最多 32 个等待请求**。不同 `conversation_id` 可同时处理，同一 ID 在当前 REST 服务内串行处理。更多业务会话不需要长期占用标签页：请求完成后槽位可以服务其他会话，续聊时按 ID 切回。

**行为变化：开启池时，未传 `conversation_id` 的请求始终新建网页会话。** 不再自动沿用服务端“上一次会话”。这保证多个调用方不会因省略 ID 而共用聊天。`rest_pool_size=1` 的旧模式仍保留原来的隐式续聊行为，但业务接入应始终明确新建或续聊。

已经正确管理会话 ID 的客户端，通常无需更改接口调用。依赖隐式续聊或全局保存一个 ID 的客户端，需按以下清单修改。

## 2. 前端和业务后端需要改什么

| 检查项 | 要求 | 通常由谁处理 |
|---|---|---|
| 会话状态 | 每个业务会话分别保存 `conversation_id`、发送中状态、消息列表；不能使用一个全局 ID | 前端；有持久化需求时由业务后端保存 |
| 首次发送 | `new_conversation: true`，省略 `conversation_id` | 组装请求的一方 |
| 后续发送 | 传该会话保存的 `conversation_id`，省略新建标记或设 false，只传本轮新增内容 | 组装请求的一方 |
| 防重复提交 | 同一业务会话上一轮未完成时禁用发送；另一会话仍可发送 | 前端，业务后端也应按会话加锁 |
| 结果归属 | 响应写回发起请求的业务会话，不能写入“此刻选中的会话” | 前端 |
| ID 获取 | JSON 读顶层 `conversation_id`；SSE 读成功 stop 事件中的 ID | 解析响应的一方 |
| 失败处理 | SDK/HTTP 原样重试关闭；网关可放弃旧尝试、新建会话自动重试一次，并过滤迟到结果 | 前端与业务后端 |
| 用户权限 | 校验该用户能访问这个业务会话；NAS API Key 留在业务后端 | 业务后端 |

`conversation_id` 是 ChatGPT 网页返回的实际 ID，不能用前端自己的任务号、随机 UUID、`user` 字段或 `metadata` 替代。本次没有实现按这些字段自动绑定会话，也没有新增幂等键。

服务端串行化是最后一道保护。调用方仍需等上一轮完成后再发下一轮，尤其是首次新建尚未获得 ID 时；两次同时携带 `new_conversation: true` 会创建两个独立聊天，不会自动合并。

## 3. 请求与响应示例

首次发送：

```json
{
  "model": "auto",
  "stream": false,
  "new_conversation": true,
  "messages": [{"role": "user", "content": "记住本次测试编号 DEMO-A。只回复 OK。"}]
}
```

确认 HTTP 200、`choices[0].finish_reason == "stop"`、非空正文和非空 `conversation_id` 后，保存 ID 到**发起请求的业务会话**。

续聊（ID 为占位符，必须替换为实际返回值）：

```json
{
  "model": "auto",
  "stream": false,
  "conversation_id": "actual-id-returned-for-A",
  "messages": [{"role": "user", "content": "刚才的测试编号是什么？"}]
}
```

普通续聊响应中的 ID 应与请求 ID 相同；不同则停止自动处理，不覆盖原映射。若网关已明确放弃旧尝试并发起新会话重试，则在校验当前 `attempt_id` 后保存新 ID。A、B 各自保存自己的 ID，可以同时请求。每次都新建会丢失续聊上下文；正常续聊时携带 ID 又重发整段历史会重复上下文。替换重试如何重建历史和图片见[新会话重试约定](NEW-CONVERSATION-RETRY.md)。

使用 OpenAI 兼容 SDK 时，新建字段放在 `extra_body={"new_conversation": True}`，续聊放在 `extra_body={"conversation_id": cid}`，并设置 `max_retries=0`。客户端封装需要保留自定义响应字段。

图片使用相同会话规则。第一轮上传图片并保存 ID，下一轮可只发文字追问。SSE 仍在完整回复核对后输出正文，并非逐 token 输出；必须检查顶层 `error`、`finish_reason` 与最终 `[DONE]`，HTTP 200 本身不代表成功。

以下是单次直通调用的前端状态示意，尚未包含新会话替换重试。`api.chatCompletions` 指项目已有业务后端调用，不能把 NAS 密钥写进浏览器代码。启用网关替换重试后，应按尝试版本接受新的 ID，并相应调整下面的 ID 一致性检查：

```typescript
async function sendTurn(session: Session, text: string) {
  if (session.pending || session.needsReview) return;
  const target = session; // 固定结果归属，切换聊天面板不改变 target
  const previousId = target.conversationId;
  target.pending = true;
  try {
    const result = await api.chatCompletions({
      model: "auto", stream: false,
      ...(previousId
        ? { conversation_id: previousId }
        : { new_conversation: true }),
      messages: [{ role: "user", content: text }],
    });
    const choice = result.choices?.[0];
    if (result.error || choice?.finish_reason !== "stop"
        || !choice.message?.content?.trim() || !result.conversation_id
        || (previousId && result.conversation_id !== previousId)) {
      throw new Error("回复未通过完整性或会话校验");
    }
    target.conversationId = result.conversation_id;
    target.messages.push({ role: "assistant", content: choice.message.content });
    // 按项目需要持久化 target；不要赋值给当前选中的其他会话。
  } catch (error) {
    target.needsReview = true;
    showRequestError(target, error); // 展示原始结构化错误；不自动再次发送
  } finally {
    target.pending = false;
  }
}
```

这是需要接入项目状态管理的示意，不是完整组件。网关可以对暂时性错误统一执行一次新会话重试，包括 `unknown` 和 `confirmed`；先放弃旧尝试并过滤迟到结果，再重建上下文和图片。最终仍失败时再展示错误，详见[完整流程](NEW-CONVERSATION-RETRY.md)。

## 4. 排队、错误和恢复

排队、切换会话、上传、输入和等待回复共用服务端请求截止时间，默认 **120 秒**；客户端建议 **180 秒**。排队没有单独延长预算。超时或客户端断开会移除等待请求，之后不会偷偷发送。通常在业务后端把整体并发限制为 `/health.rest_pool.size`，可以减少长时间排队；这个上限是整个服务共享的，不是每个用户各有两路。

| 返回 | 含义与处理 |
|---|---|
| `503 browser_pool_busy` | 等待队列已满或服务关闭中；退避后可在网关的一次重试预算内新建重试 |
| `504 request_timeout`，`phase=queue` | 排队耗尽预算，`send_state=not_sent`；不会稍后补发 |
| `503 browser_unresponsive` | 所需槽位/会话暂停，或全部槽位暂停；其他健康会话可能仍可用 |
| `429 rate_limit_exceeded` | 同一账号共享冷却期，等待 `Retry-After` 后才可新建重试；不能换会话绕过限流 |
| `send_state=unknown` | 可能已经发送；可放弃旧尝试并新建重试一次，旧结果必须丢弃 |
| `send_state=confirmed` 但失败 | 已确认提交；同样允许放弃旧尝试、新建重试一次 |

错误沿用 `error.phase`、`error.send_state`、`error.prompt_sent`。`automatic_retry_allowed=false` 禁止普通 SDK 原样重放；新增 `error.new_conversation_retry` 明确允许网关在新会话替换重试，包含 `allowed`、`mode`、`max_retries` 和 `retry_after_seconds`。整个业务任务最多自动重试一次，由网关执行预算和结果版本校验。响应诊断中的 `request_diagnostics.browser_slot` 仅供排障，不是客户端路由参数。

单个槽位超时或发送结果不确定时会暂停，并保留已知的会话绑定，避免同一 ID 换到另一个健康槽位继续发送。其他健康槽位可以服务其他会话。账号限流和 Chrome 整体故障仍会影响全部槽位。

需要恢复故障槽位时，可带现有 Bearer 密钥调用 `POST /v1/browser/recover?slot=0`。新会话重试可直接使用其他健康槽位；旧任务既已放弃，无需先确认它是否完成。没有可用槽位时才在重试流程中做只读恢复；不为单个失败请求重启整个服务。不传 slot 时探测暂停槽位；没有暂停槽位时探测全部槽位。忙碌的槽位返回 503，不中断当前请求。恢复不重放消息、不清除账号冷却。

## 5. 服务端配置、确认与回退

配置文件字段：

```json
{
  "tab_mode": "owned",
  "parallel_tabs": true,
  "rest_pool_size": 2,
  "rest_pool_max_queue": 32
}
```

对应环境变量是 `W2A_TAB_MODE`、`W2A_PARALLEL_TABS`、`W2A_REST_POOL_SIZE`、`W2A_REST_POOL_MAX_QUEUE`。仓库 NAS `compose.yaml` 已配置 2 路，需重建镜像并重新创建容器才会生效。普通非 Compose 安装默认仍为单路。只开启 `parallel_tabs` 不会创建 REST 池。

`GET /health` 新增 `rest_pool`，包含 `enabled`、`size`、`active`、`queued`、`max_queue`、`account_retry_after` 和各槽位状态。单路时返回 `{"enabled": false, "size": 1}`；老版本没有该字段。部分槽位异常时总体 `status=degraded`，顶层 `browser_paused` 只有在全部槽位暂停时才为 true。

回退只需把部署配置中的 `W2A_REST_POOL_SIZE` 改成 `1` 并重建容器；无需改变客户端已保存的会话 ID，显式续聊仍然可用。切换配置前等待在途请求结束。

在 NAS 项目目录执行 `sh scripts/deploy-rest-concurrency.sh` 可备份旧镜像、构建并核对源码后重建服务。失败时自动回退；手动回退命令为 `sh scripts/rollback-rest-concurrency.sh`。脚本停止服务后只清理 Chrome 的三个旧单例锁符号链接，保留登录配置。实际聊天验收为显式执行 `docker exec -i chatgpt-web2api python - --send < scripts/check-rest-concurrency.py`，它创建两个合成测试会话并发送四条请求，不自动重发、不删除历史。

同会话串行保证的范围是**单个 REST 服务进程**。不要把同一会话轮流分发到多个独立 REST 实例或同时在网页/MCP 中发送；跨实例的会话调度需由业务后端实现。所有槽位共享一个登录账号，并发不增加账号额度。

## 6. 调用方验收清单

1. 新建 A、B，确认两个 ID 不同，并保存到正确的业务会话。
2. 同时追问 A、B，确认各自只能回忆自己的测试编号，响应不会因切换面板而写错位置。
3. A 正在发送时不能再点发 A，但仍能发 B；首次新建也要防重复点击。
4. 同时提交第三个独立会话，确认排队；排队请求取消或超时后不会再发送。
5. 验证图片上传后按 ID 文字追问；验证 SSE 成功 stop 中的 ID 和最终 `[DONE]`。
6. 验证 400/401 不自动重试；超时、5xx、429/SSE error 可按策略新建重试一次，旧结果不回填，第二次失败停止，其他会话不中断。

221 项相关离线测试通过，其中 23 项为新增池测试，另有 26 项图片上传与服务器确认测试，覆盖 HTTP 两路重叠、同 ID 串行、容量/取消、JSON/SSE/图片参数隔离、失败槽位隔离、账号冷却和启动清理；浏览器行为使用合成替身。其中也包括 5 项新增的新会话地址变化回归和 34 项消息归属选择测试，覆盖严格会话校验及同文不同消息的误匹配防护。


## 7. 本次 NAS 部署与实测（2026-09-25）

已重建并部署，`rest_pool.enabled=true`、`size=2`。合成验收脚本同时提交 A、B，再同时续聊；监测到 `peak_active=2`，四条请求均通过，无自动重发。

| 请求 | 结果 | 耗时 |
|---|---|---|
| A：新建文字会话 | 返回指定测试编号及独立会话 ID | 40.08 秒 |
| B：新建蓝色图片会话 | 识别蓝色后返回自己的测试编号；ID 与 A 不同 | 35.28 秒 |
| A：按 ID 文字追问 | 正确回忆 A 的编号，ID 保持不变 | 6.52 秒 |
| B：按 ID SSE 追问 | 正确回忆 B 的编号，ID 保持不变，收到 stop 与 `[DONE]` | 6.77 秒 |

现代/旧版上传表单的真实浏览器 DOM 检查也通过，覆盖文件输入框定位、歧义拒绝、本地预览的服务器完成确认、精确清理。最终健康检查无暂停槽位、无排队、无活动请求。以上为两路合成功能验收；长时间运行和更大并发需要另行测试。前端仍须在自己的业务状态管理中执行第 6 节验收清单。
