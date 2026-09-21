# REST 错误码与后端处理手册

[English](ERROR-CODES.en.md) · [API 使用手册](../API使用与项目接入手册.md) · [请求超时与浏览器恢复](REQUEST-RECOVERY.md)

本表按 2026-09-22 工作树中的实现核对，适用于包含请求保护、图片输入和导航恢复更新的 REST 服务。旧镜像可能缺少部分代码或字段；文档更新不会自动更新部署。本表不把 MCP 工具错误当作 REST 契约。

## 1. 后端首先判断什么

包含安全准备恢复更新的服务增加顶层 `request_diagnostics`；部分导航超时会先在原请求内进行只读恢复再返回最终结果。具体白名单、次数、预算、诊断字段与示例见[请求内安全恢复](SAFE-RECOVERY.md#中文)。这不改变客户端禁止自动重放的规则。

**HTTP 状态用于判断请求是否成功，`error.code` 用于分类，发送状态用于决定是否需要人工确认。不能仅凭 500、504 或“超时”推断提示词没有发送。**

1. 保存 HTTP 状态，有限量读取错误体；例如最多读取 64 KiB，避免把大响应写入日志。
2. JSON 中存在顶层 `error` 就按失败处理。SSE 在 HTTP 200 后也可能包含错误。
3. 优先读取 `error.code`；缺失时结合 `error.type` 和 HTTP 状态归类，不按英文 `message` 做稳定分支。
4. 严格校验 `send_state` 与 `prompt_sent`。字段缺失、类型错误或相互矛盾时，按发送状态不确定处理。
5. 关闭 SDK、任务队列和反向代理对聊天请求的自动重试。当前受保护聊天错误明确返回 `automatic_retry_allowed=false`；字段缺失也不表示允许重试。
6. 人工确认原请求结果、浏览器状态及会话后，再决定是否发起一次新的请求。不要通过自动换会话规避错误。

这些接口没有提供幂等提交或失败请求去重保证。`Retry-After` 也不是自动重发许可。

## 2. 发送状态与处理阶段

受请求保护器处理的聊天运行错误会附加以下字段；提前返回的鉴权/参数错误、恢复接口及框架错误并不具备完整字段。

| `send_state` | `prompt_sent` | 含义 | 后端动作 |
|---|---|---|---|
| `not_sent` | `false` | 本次请求尚未执行发送动作；可能已经导航、填入文字或上传附件 | 修复问题，确认页面状态后由用户决定重试 |
| `confirmed` | `true` | 本次发送已得到确认，不代表回复成功收集 | 查看原会话，避免再发相同问题 |
| `unknown` | `null` | 已尝试发送，但没有足够证据确认结果 | 暂停该浏览器上的后续业务请求，检查原会话 |

JSON 布尔值必须严格判断；字符串 `"false"`、数字 `0` 都不能当作合法的 `false`。客户端超时、连接中断、非 JSON 错误或旧版本缺少字段时，也不能自行推导为 `not_sent`。

`phase` 表示最后记录的处理阶段，不单独证明发送结果：

| 值 | 阶段 |
|---|---|
| `validation` | 请求解析、校验或尚未进入后续阶段 |
| `queue` | 等待浏览器操作锁 |
| `prepare` | 浏览器或会话准备 |
| `model_selection` | 选择模型 |
| `navigation` | 打开指定会话或新会话 |
| `input` | 填入文字 |
| `upload` | 上传并检查图片附件 |
| `send_ready` | 检查发送前条件 |
| `send` | 已开始发送动作 |
| `reply` | 等待、读取并核对回复 |

## 3. 聊天运行错误码总表

表内 HTTP 状态指尚未发送 SSE 响应头时的状态。`server_error` 通常是 `error.type`，**不是所有 500 都带有 `error.code="server_error"`**。

| HTTP | `error.code` | 触发条件 | 建议处理 |
|---|---|---|---|
| 401 | `invalid_api_key` | 驱动识别到 ChatGPT 网页登录失效；此历史命名容易与本地 Key 混淆 | 在远程桌面核对 ChatGPT 登录，不要只反复更换本地 Key |
| 429 | `rate_limit_exceeded` | 网页触发限流 | 尊重 `Retry-After` 秒数并停止新任务；核对发送状态，不自动重发 |
| 500 | **无 code**，`type=server_error` | 未单独分类的内部异常 | 检查发送状态、发生阶段和同一时间附近的服务日志 |
| 502 | `navigation_failed` | 导航命令明确报告失败 | 检查页面与 `error.navigation`，不要自动创建替代会话 |
| 502 | `navigation_displaced` | 曾到达目标地址，随后连续探测确认页面偏离目标 | 检查登录跳转或其他页面操作；本次导航路径尚未发送 |
| 503 | `browser_unresponsive` | 浏览器操作保护器已暂停，拒绝新操作 | 查看 `/health` 和远程桌面；确认原任务后执行只读恢复 |
| 503 | `lock_timeout` | 未在锁预算内取得浏览器操作锁 | 等待已有任务结束，检查是否多个客户端同时使用；不要循环重试 |
| 503 | `circuit_open` | 某类错误的熔断器仍处于冷却状态 | 检查 `open_breakers` 和网页状态，解决原因并等待冷却 |
| 503 | `owned_tab_required` | 并行标签模式下缺少有效的专属标签或标签所有权失效 | 管理员检查标签与并行配置，不能偷偷改用另一个标签发送 |
| 504 | `request_timeout` | 整次请求预算耗尽，默认 120 秒，涵盖排队至回复 | 依据发送状态判断，检查 `phase`，不能认为未发送 |
| 504 | `browser_timeout` | 单次浏览器/CDP 操作超时 | 检查浏览器响应和暂停标志；不单凭此码重启或重发 |
| 504 | `client_disconnected` | 服务端检测到调用方断开连接并停止请求 | 客户端通常已无法接收此响应；按结果不确定核对原会话 |
| 504 | `navigation_timeout` | 指定会话未在导航预算内稳定就绪，当前预算 30 秒 | 查看导航诊断；此路径尚未发送；整次预算先耗尽时可能返回 `request_timeout` |
| 504 | `image_upload_timeout` | 图片上传未能在预算内确认完成 | 检查附件状态、大小和网络；正常上传失败路径不发送提示词 |
| 504 | `reply_timeout` | 无法在回复核对截止时间前取得可信的最终回复 | 先检查原会话；网页可能已经回答，不要重复提交 |
| 504 | `generation_stuck` | 检测到网页生成停滞 | 检查网页是否仍生成、网络与账户状态，再决定如何恢复 |

统一以实际响应里的三态字段为准，不能用错误码覆盖它们。截止时间、断连与浏览器暂停同时发生时，保护器可能优先选择 `request_timeout` 或 `client_disconnected`，而 HTTP 状态仍来自原异常类别；不要只按“HTTP + code”的固定组合解析。

未分类 500 的例子包括非超时图片上传失败、部分 JavaScript/CDP 异常、非截止时间原因的回复归属核对失败及其他运行异常。这不是完整清单；500 本身无法定位根因，也不代表必须重新登录。

## 4. 没有独立 code 的错误

| HTTP / 形式 | 当前响应或原因 | 后端动作 |
|---|---|---|
| 401 | `type=auth_error`，`message="Invalid API key"`，无 `code` | 核对本服务配置的 Bearer Key，与 ChatGPT 网页登录分别排查 |
| 400 | `type=invalid_request_error`，无 `code` | 修正请求结构或图片输入，不原样重发 |
| 404 | 路由不存在，例如拼错路径、重复 `/v1`、调用不支持的端点或旧镜像没有新接口 | 核对 Base URL、路径和部署版本 |
| 405 | HTTP 方法不支持 | 聊天和恢复接口使用 POST，查询接口使用 GET |
| 413 | 请求体超过框架的 10 MiB 限制 | 减小完整 JSON；Base64 后的体积也计入请求体 |
| 500 / 非 JSON | 框架错误、未被统一映射的异常，或中间代理返回的错误 | 保留 HTTP 状态与脱敏摘要，发送结果保守视为不确定 |
| 无 HTTP 响应 | 连接拒绝、客户端超时、断网、代理中断 | 区分网络问题与服务器返回的 504；不要假设服务端任务已撤销 |

框架/代理错误体可能是纯文本或 HTML，不能假设所有失败都符合 `{"error": ...}`。当前输入校验不是覆盖所有字段类型的完整 schema，不能保证所有畸形请求都会返回 400。

### 常见 400 原因

- JSON 无法解析、顶层不是对象、`messages` 不是非空对象数组，或没有 user 消息。
- `new_conversation` 不是 JSON 布尔值；`conversation_id` 不是非空字符串或 null；同时传入 `new_conversation=true` 和 `conversation_id`。
- `content` 不是字符串或内容数组；内容项不是对象；内容项不是受支持的 `text` / `image_url`。
- 图片不在最后一条 user 消息中；后续问题应使用会话 ID 保留网页原有附件。
- `image_url.url` 不是字符串，或 `detail` 不是 `auto`。
- 没有使用 PNG、JPEG、WebP 的 Base64 data URL；HTTP 图片地址、本机路径不受支持。
- Base64 无效、图片为空、实际格式与 MIME 不符、图片损坏或为动画。
- 超过每次 4 张、每张解码后 4 MiB、合计解码后 6 MiB、每张 2000 万像素的限制。

这些条件当前共用 400 + `invalid_request_error`，没有 `invalid_image`、`too_many_images` 等细分机器码。详细上传规范见[图片输入](IMAGE-INPUT.md)。

## 5. 导航错误诊断与示例

以下是合成示例，不包含真实请求信息：

```json
{
  "error": {
    "code": "navigation_timeout",
    "type": "server_error",
    "message": "Conversation navigation failed: navigation_timeout; stage=composer_unavailable",
    "phase": "navigation",
    "send_state": "not_sent",
    "prompt_sent": false,
    "automatic_retry_allowed": false,
    "navigation": {
      "stage": "composer_unavailable",
      "url_matches": true,
      "ready_state": "interactive",
      "app_shell_present": true,
      "composer_present": true,
      "composer_usable": false,
      "probe_error": null,
      "elapsed_ms": 30000
    }
  }
}
```

`navigation` 仅针对指定会话的导航错误，不保证所有 `phase=navigation` 的错误都有此对象。

| `navigation.stage` | 含义 |
|---|---|
| `probe_unavailable` | 没有可用的页面就绪探测结果 |
| `url_mismatch` | 页面地址不匹配目标会话 |
| `document_loading` | 文档尚未进入可接受的加载状态 |
| `app_shell_missing` | 未检测到应用主体 |
| `composer_unavailable` | 输入框不存在、不可见或不可用 |
| `stability_check` | 已满足条件，但尚未取得连续两次稳定结果 |

`ready_state` 为 `loading` / `interactive` / `complete` 或 null；`interactive` 本身不是失败。`probe_error` 当前为 null 或 `readiness_probe_failed`。`elapsed_ms` 是本次导航耗时，不是整个请求耗时。其余字段为布尔值，不包含真实 URL、会话 ID、Cookie 或正文。

## 6. 流式响应也必须判断错误

响应头尚未发出时，失败可使用上表的 HTTP 状态。SSE 头已发出后，HTTP 仍是 200，受保护路径使用如下事件报告失败：

```text
data: {"error":{"code":"reply_timeout","type":"server_error","message":"Reply deadline exceeded","phase":"reply","send_state":"confirmed","prompt_sent":true,"automatic_retry_allowed":false},"choices":[{"index":0,"delta":{},"finish_reason":"error"}]}

data: [DONE]
```

这是合成示例，实际消息文字可能不同。顶层 `error` 或 `finish_reason="error"` 均表示失败；`[DONE]` 仅表示流结束。断连或错误事件写入失败时，可能连错误事件和 `[DONE]` 都收不到。

后端应要求正常结束、无错误且存在有效业务内容，再认定成功。兼容旧响应时也要识别 `[Error: ...]` 文本，但不要依赖其英文措辞做细分分类。建议首先接入非流式请求。

## 7. 管理员恢复接口

`POST /v1/browser/recover` 携带本地 Bearer Key，不需要请求体。它取得浏览器锁后执行只读响应探针，成功才清除浏览器暂停；总预算 5 秒，探针预算 3 秒。

| HTTP | 结果 | 含义 |
|---|---|---|
| 200 | `status=ready`、`prompt_sent=false` | 探针通过并解除暂停，没有重放旧请求 |
| 401 | `type=auth_error`，无 code | 本地 Key 错误 |
| 503 | `code=browser_busy`、`prompt_sent=false` | 未取得操作锁，包括锁定位失败；不打断正在运行的任务 |
| 503 | `code=browser_unresponsive`、`prompt_sent=false` | 已取得锁，但只读探针失败，维持暂停 |

恢复失败体的 `error` 当前仅有 `code`、`message`、`prompt_sent`，没有完整聊天错误字段。这些字段描述的是**恢复请求本身**，不是之前失败的聊天。

恢复不会导航、重启服务、重新连接浏览器或重发问题。成功只证明当时探针有响应，不证明账户登录有效、旧请求已完成或下一次生成一定成功，也不会清除所有独立熔断器。

建议流程：暂停业务调用 → 检查 `/health` 和远程桌面 → 核对原会话结果 → 必要时修复页面/登录 → 调用恢复接口 → 确认解除暂停 → 再由用户决定下一次请求。不要把恢复接口加入每次失败后的自动重试循环。

## 8. 健康状态与日志

| 字段 / 值 | 如何理解 |
|---|---|
| `browser_paused` | REST 浏览器操作是否暂停 |
| `browser_pause_reason=consecutive_cdp_timeouts` | 连续两次 CDP 命令超时 |
| `browser_pause_reason=send_outcome_unknown` | 发送结果不确定，防止后续操作干扰现场 |
| `browser_pause_reason=request_interrupted` | 请求在持锁操作阶段被中断或整体超时 |
| `browser_pause_reason=recovery_probe_failed` | 管理员只读恢复探针失败 |
| `consecutive_cdp_timeouts` | 连续超时计数，不是全部历史错误数量 |
| `last_error_at` | 最近记录错误的 Unix 秒级时间戳，可为 null |
| `last_error` | 历史错误摘要，不能单独证明现在仍故障；可能包含敏感信息 |
| `open_breakers` | 当前独立熔断器状态，需结合网页登录和错误原因检查 |
| `requests_served` | 请求计数，不是成功生成次数 |

读取 `/health` 不会解除暂停。刚启动的 `starting` 不一定是故障；`healthy` 也不保证下一次调用成功。`/v1/models` 可以返回回退列表，项目列表可以回退为空，因此查询 HTTP 200 不能替代真实聊天验证。

后端建议记录：自有请求追踪号、时间与时区、客户端耗时、HTTP 状态、错误 code/type、phase、三态发送字段，以及经过白名单筛选的导航诊断。自有追踪号用于关联本地记录，不代表服务端提供幂等或请求查询能力。

不要将 Bearer Key、Cookie、浏览器配置、Base64 图片、业务正文、真实会话 URL 或完整错误体直接写入公开报告。`message` 和服务原始日志并非全部已脱敏；上传日志前需审查。公开错误示例使用合成数据。

## 9. 可直接采用的界面提示

| 情况 | 建议提示 |
|---|---|
| 参数错误 | 请求格式或图片不符合要求，请修改后提交。 |
| 本地鉴权失败 | 接口密钥验证失败，请联系管理员检查配置。 |
| 网页登录失效 | ChatGPT 登录已失效，请管理员在远程桌面登录。 |
| 已确认未发送 | 本次未发送，请修复问题并检查页面后再提交。 |
| 已确认发送，但读取失败 | 问题已经发送，回复读取失败；请先查看原会话，避免重复提交。 |
| 发送结果不确定 | 暂时无法确认是否已发送，请先查看原会话。 |
| 浏览器暂停 | 服务已暂停浏览器操作，等待管理员检查和恢复。 |

“来源链接不可用”、缺少引用元数据或模型输出不符合业务 JSON 格式，不必然是 HTTP 接口错误。业务层应分别验证和展示，不能统一记成 500。引用契约见[引用信息](CITATIONS.md)。

## 10. 契约范围与维护

来源：[REST 错误映射](../src/chatgpt_web2api/api_server.py)、[请求保护与发送状态](../src/chatgpt_web2api/request_guard.py)、[导航实现](../src/chatgpt_web2api/cdp_driver.py)、[图片校验](../src/chatgpt_web2api/image_input.py)。

当前仍有无 code 的 400/401/500、部分非 JSON 错误和历史命名；本手册记录现状，没有通过文档新增错误码。后续若统一响应结构，应同步更新中英文手册、接口实现和对应测试，并在版本说明中标明兼容性变化。
