# 后端会话接入 / Backend conversation integration

[中文 API 手册](../API使用与项目接入手册.md) · [English API guide](../API-USAGE.md) · [图片识别](IMAGE-INPUT.md)

更新：2026-09-25。此前的串行会话功能已通过 NAS 真实文字、图片、SSE 追问和切换会话测试。本次新增 REST 两路并发；前端与业务后端先阅读[并发迁移说明](REST-CONCURRENCY.md)，运行模式以 `/health.rest_pool` 为准。本文的业务状态和存储设计是给调用方的接入建议，不是 web2api 自动提供的功能。

## 1. 是否适合后端采用

可以。同一个任务需要多轮讨论、对同一张图片追问，或反复使用同一份材料时，可保存网页会话 ID 后续聊。独立任务仍应明确新建，避免旧问题和旧候选影响本轮判断。

例如文档问答，可按“同一份材料的一次问答任务”管理一个业务会话；换材料、换任务或规则版本后新建。要求每次完全独立判断时，每次都设 `new_conversation: true`。不要让所有后台任务共用一个固定 ID。

会话方式可以减少重复发送历史文字，但不会把网页服务变成无上下文限制的接口，也没有准确 token 用量统计。模型输出仍需 JSON、schema、任务 ID 和候选范围校验。

## 2. 请求与响应约定

接口仍是 `POST /v1/chat/completions`，密钥和 Base URL 不变。下面两个字段放在请求 JSON 顶层，属于本项目扩展。

| 客户端意图 | 请求字段 | 服务行为 |
|---|---|---|
| 新建聊天 | `"new_conversation": true`，不传 ID | 开新网页聊天，完成响应中返回新 ID |
| 继续聊天 | `"conversation_id": "已保存的ID"`，省略新建标记或传 false | 切换到对应聊天并发送本轮内容 |
| 新建同时指定旧 ID | true 和非空 ID 同时出现 | HTTP 400，尚未操作浏览器 |
| 两者都不传 | 无 | REST 池模式始终新建；单路旧模式可能隐式续聊，业务接入不要依赖 |
| 停止使用某个会话 | 后端把本地会话标为结束 | 不再往该 ID 发请求；web2api 没有对应的结束/删除 REST 接口 |

`new_conversation` 必须是布尔值，不能用字符串 `"true"`。`conversation_id` 为非空字符串或 null；不能传数字、空字符串或纯空白。ID 必须来自当前登录账号实际可访问的网页会话，不能用后端自己的任务编号代替。

**新建：**

```json
{
  "model": "auto",
  "new_conversation": true,
  "stream": false,
  "messages": [
    {"role": "user", "content": "这是一个新的文档问答任务。规则和材料如下：……请先确认已理解。"}
  ]
}
```

成功响应的重要字段示意（ID 是占位符）：

```json
{
  "id": "chatcmpl-response-id",
  "conversation_id": "actual-web-conversation-id",
  "choices": [{
    "message": {"role": "assistant", "content": "已理解。"},
    "finish_reason": "stop"
  }]
}
```

保存的是 `conversation_id`，不是 `id` 中的 `chatcmpl-...`。

**续聊：**

```json
{
  "model": "auto",
  "conversation_id": "actual-web-conversation-id",
  "stream": false,
  "messages": [
    {"role": "user", "content": "按刚才的规则处理这一组新材料：……"}
  ]
}
```

只提交本轮新增内容。不要一边传原会话 ID，一边把整段 user/assistant 历史重发。可以明确重申必要规则，但 system 在本项目中也是拼入网页输入框的文字，并不是一个独立的权限通道。

图片请求采用相同规则：首次把 Base64 图片放在最后一条 user 消息中；后续保存并传回会话 ID，就可以只发文字追问，不需要再上传。格式和大小限制见[图片文档](IMAGE-INPUT.md)。

## 3. 后端需要保存什么

| 建议字段 | 用途 |
|---|---|
| `logical_session_id` | 后端自己的会话或任务编号 |
| 所属用户、任务/批次、规则版本 | 确定哪些请求可以共享上下文 |
| 服务实例/账号标识 | 避免换 NAS 服务或登录账号后误用旧 ID；无需存 Cookie |
| `conversation_id` | web2api 返回的网页会话 ID |
| `state` | 例如 ready、in_flight、uncertain、closed，由后端自己实现 |
| 最近成功的请求编号与业务校验结果 | 用于排查重复提交、失败和结果恢复 |

建议流程：

1. 创建业务会话时，记录一个待执行的新建请求，再发送 `new_conversation: true`。
2. 收到 HTTP 200、`finish_reason=stop`、有效文字和非空 ID 后，保存 ID；新建请求的原样载荷不能用于下一轮，否则会再次新建。
3. 对已有业务会话加锁，标记 in_flight，携带已保存 ID 发送下一轮；响应 ID 应与原 ID 相同。
4. 传输成功后保留 ID，再单独验证业务答案。即使 JSON/schema 不合格，该轮也可能已经进入网页历史，不应把它当成“没有发送过”。
5. 任务结束时关闭后端本地会话映射。下一项独立任务明确新建；这不会删除网页历史。

REST 池模式允许不同会话并发，NAS Compose 配置为两路，超出排队；同一个 ID 在当前 REST 进程内串行。业务后端仍需保证同一会话的发送顺序，并对首次新建防止重复提交。等待计入请求截止时间，前端迁移和验收步骤见[并发接入说明](REST-CONCURRENCY.md)。客户端自己的用户权限和会话映射仍需自行校验，本服务的 API key 不是逐会话权限隔离机制。

## 4. 失败时怎么处理

| 情况 | 后端处理建议 |
|---|---|
| 400 参数冲突/类型错误 | 修正请求；这些校验发生在发送之前 |
| HTTP 错误、超时、连接中断 | 不盲目重发；记录为结果不确定，先检查网页和日志 |
| 显式 ID 无法访问或不存在 | 报错并检查账号、ID、网页状态；不要自动悄悄新建 |
| HTTP 200 但文字为空、缺 ID 或 finish_reason 不是 stop | 不当作成功业务结果；保留现场，检查服务 |
| 返回 ID 与续聊请求 ID 不同 | 停止自动处理，不覆盖本地映射 |
| 文字完整但业务 JSON/schema 不合格 | 保留会话 ID，记为业务校验失败；由业务规则决定是否追问修正 |
| 后端进程重启 | 从自己的持久化记录恢复映射，不依赖 web2api 的“上次会话”状态 |

连接失败不等于消息未发送，尤其是请求发出后的超时。首次新建请求超时还可能导致后端拿不到已经生成的 ID。因此不要开启自动 HTTP/SDK 重试；服务内部仍有其他重试机制，本项目不提供端到端幂等键或“恰好一次”保证。

## 5. 可直接改造的 Python 示例

设置 `NAS_CHATGPT_BASE_URL`（例如 `http://192.168.1.100:11111/v1`）和 `NAS_CHATGPT_API_KEY`。示例仅用标准库，不自动重试。**直接运行会发送两条真实请求**；后端接入时把 `turn()` 放入按业务会话串行、跨会话并发受限的队列，并将 ID 持久化到自己的数据库。

```python
import json
import os
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def turn(content, conversation_id=None):
    payload = {
        "model": "auto",
        "stream": False,
        "messages": [{"role": "user", "content": content}],
    }
    if conversation_id is None:
        payload["new_conversation"] = True
    else:
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("Invalid stored conversation_id")
        payload["conversation_id"] = conversation_id
    request = urllib.request.Request(
        os.environ["NAS_CHATGPT_BASE_URL"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + os.environ["NAS_CHATGPT_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    # Exceptions propagate: the caller must record uncertain outcomes.
    with opener.open(request, timeout=180) as response:
        if response.status != 200:
            raise RuntimeError("Unexpected HTTP status")
        result = json.load(response)
    cid = result.get("conversation_id")
    choices = result.get("choices") or []
    if not isinstance(cid, str) or not cid.strip() or not choices:
        raise RuntimeError("Incomplete response; check the webpage before retrying")
    if conversation_id is not None and cid != conversation_id:
        raise RuntimeError("Conversation ID changed unexpectedly")
    choice = choices[0]
    text = (choice.get("message") or {}).get("content")
    if choice.get("finish_reason") != "stop" or not isinstance(text, str) or not text.strip():
        raise RuntimeError("Reply was not successfully completed")
    return cid, text


if __name__ == "__main__":
    cid, text = turn("记住本次测试编号 DEMO-314。只回复 OK。")
    print(text)
    # Persist cid before submitting another request in a production backend.
    cid, text = turn("刚才的测试编号是什么？", conversation_id=cid)
    print(text)
```

OpenAI 兼容 SDK 的扩展字段可用 `extra_body={"new_conversation": True}` 或 `extra_body={"conversation_id": cid}` 传递，配置 `max_retries=0`。如果客户端丢弃自定义响应字段，改用原始 HTTP JSON 或支持保留扩展字段的封装。

## 6. 流式响应与验收范围

后端先采用 `stream: false` 最容易保存 ID 和校验结果。使用 SSE 时，`conversation_id` 在成功的最后一个 `finish_reason=stop` 事件中返回，随后是 `[DONE]`；不要从第一块数据或顶层 `chatcmpl-...` 猜 ID。必须检查错误事件、stop 和流是否完整结束。正文依然等待完整性核对后发送，不是逐 token 实时输出。

已完成的 NAS 验收：新建文字会话 A → 新建双图会话 B（A、B 不同）→ SSE 追问 B（ID 不变）→ 按 ID 切回 A（正确回忆原测试编号）。它证明这条调用流程可用，不代表长会话、复杂业务正确性或并发场景已全面验证。

## English integration summary

Backend adoption is supported. Use one stored ChatGPT conversation per logical task/batch when context should be shared. Force a new conversation for independent judgments or changed task/rule scopes. Do not share one global ID across all users or jobs.

- **Create:** top-level `new_conversation: true`, no `conversation_id`.
- **Continue/switch:** send the returned `conversation_id` and only the new user content; omit the new-chat flag or set it to false.
- **Conflict:** true plus a nonempty ID returns HTTP 400 before browser mutation. The flag is a JSON boolean, not a string. An ID must be a nonempty string or null and refer to a conversation accessible to the signed-in account.
- **Omit both:** pooled REST always starts a fresh chat. Singleton mode retains legacy implicit selection; do not rely on that shared state.
- **Close:** stop using the mapping in your backend. There is no corresponding REST close/delete endpoint, and this does not delete website history.

Persist logical session ownership, task/batch and rule version, service/account identity, returned conversation ID, request state and business-validation result. Preserve per-conversation order and prevent duplicate first sends. Pooled REST supports distinct conversations concurrently (NAS Compose: two workers); excess calls wait within the request deadline. See [REST concurrency migration](REST-CONCURRENCY.md). IDs are routing information, not per-user authorization boundaries. Switching accounts requires revalidating mappings.

Check HTTP 200, nonempty content, `finish_reason=stop` and a nonempty conversation ID. Continued responses should return the requested ID. Store the successful transport result before applying business JSON/schema/candidate validation: invalid business content can still be a completed turn in the website history. Do not resend all history together with the original ID.

Disable automatic client retries. A timeout or disconnect may occur after submission, and a timed-out new request may have created a conversation whose ID was not received. Record uncertain outcomes and inspect before resubmitting. Unavailable explicit IDs must not silently fall back to new chats. No end-to-end idempotency or exactly-once contract is implemented; internal retry mechanisms remain.

The Python example above uses standard-library HTTP, propagates failures and sends two real requests only when executed as a script. Configure the base URL and service key through its named environment variables; add persistence and queueing in your own backend. SDK callers can use `extra_body` for the extension fields and `max_retries=0`.

Images follow the same conversation rules; upload once and ask text follow-ups using the stored ID. For SSE, save the ID from the successful final stop event and verify the terminal `[DONE]`, not from the first chunk. Content is buffered until final verification. A real NAS sequence passed: new text chat A, new two-image chat B, SSE follow-up in B, and switching back to A. Long-context accuracy, concurrency and broad business correctness are not established by this small acceptance test.
