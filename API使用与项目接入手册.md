# ChatGPT-Web2API 使用与项目接入手册

[English](API-USAGE.md) · [项目首页](README.md)

更新日期：2026-09-25。适用于本 NAS Fork。192.168.1.100、NAS-HOST 都是示例，需替换。本文面向调用接口的项目；安装和修复见同目录《NAS安装与故障排查手册.md》。

**本次多会话并发接入：** 请前端和业务后端先看[需要修改的地方、请求示例和验收清单](docs/REST-CONCURRENCY.md)。接口和成功响应格式兼容；池模式下未传会话 ID 始终新建，不再隐式续接上一次聊天。

**失败允许新建重试：** 网关可放弃旧尝试，在新会话自动重试一次，包括原消息已发送或结果不确定的情况。必须重建上下文和图片、过滤旧结果；普通 SDK 原样重试仍关闭。见[重试约定](docs/NEW-CONVERSATION-RETRY.md)。

## 1. 接入参数

| 配置项 | 填写值 |
|---|---|
| 服务类型 | OpenAI Compatible / OpenAI 兼容 / 自定义 OpenAI |
| Base URL | `http://192.168.1.100:11111/v1` |
| 完整聊天 URL | `http://192.168.1.100:11111/v1/chat/completions` |
| 模型 | `auto` |
| API Key | 从 NAS 项目的 `data/api.env` 中读取 `W2A_API_KEYS` 的值 |
| 请求头 | `Authorization: Bearer <API Key>` |
| 初次接入 | `stream: false`，并发 1，关闭普通 HTTP/SDK 原样重试；网关按上述约定实现新会话重试 |
| 建议客户端超时 | 180 秒；这是客户端设置，不会修改服务端超时 |

REST 现将排队、导航、上传和读取回复纳入同一服务端截止时间（默认 120 秒）。三态发送结果、浏览器暂停状态和带密钥的只读恢复接口，见[请求超时与浏览器恢复](docs/REQUEST-RECOVERY.md)。

如果配置框要求 Base URL，填到 `/v1`；如果要求完整接口地址，填到 `/v1/chat/completions`。避免拼接成 `/v1/v1/chat/completions`。

`data/api.env` 的 Windows 路径是 `\\NAS-HOST\docker\chatgpt-web2api\data\api.env`。只复制等号后的密钥值，不复制变量名；多个密钥用逗号分隔时，选其中一个。它是本服务的访问密钥，不是官方 OpenAI API Key，也不是 Google 密码。手册不包含真实密钥。

API Key 由部署者首次安装时自行生成，不是从网页账号导出，也不会随容器重启改变。新安装的生成命令和更换方法见[安装手册：API Key 是什么，如何生成](NAS安装与故障排查手册.md#api-key-是什么如何生成)。已有部署直接向管理员获取现有密钥；客户端密钥框只填密钥值，不包含 `Bearer `。VNC 密码不能用于调用 API。

调用项目必须能访问 NAS 局域网。另一个 Docker 容器也用 NAS IP 和 11111 端口，不能用 `localhost` 指向 NAS。云端部署的项目通常不能直接访问 `192.168.1.100`，需要另行配置可达的私有网络。

## 2. 能做什么，不能做什么

网页引用通过 `message.annotations` 返回（SSE 为 `delta.annotations`），需要客户端显示链接。正文保持原样，旧记录不会自动补齐来源。见[引用格式与限制](docs/CITATIONS.md)。

| 功能 | 当前情况 |
|---|---|
| 普通文本请求 | 已通过最小 OK 测试及合成结构化样本；不代表表中所有任务类型均已实测 |
| 多轮文本 | 代码支持历史 messages 和自定义 conversation_id；用法见第 5 节 |
| 流式 SSE | 保留 SSE 格式，正文等待最终核对后发送，不再实时逐字输出；NAS 图片追问 SSE 最小测试通过，更广泛的流式验收仍待完成，初次接入先关闭 |
| 图片理解、图片附件 | 最后一条 user 消息支持 PNG/JPEG/WebP Base64 图片；见[图片接入说明](docs/IMAGE-INPUT.md) |
| PDF、Word、Excel 等文件上传 | 当前 API 不支持，无 `/v1/files` 上传端点 |
| 读取文档内容 | 原文档先提取文字；扫描 PDF 可由调用方转换为受支持的图片，再按图片接口发送 |
| Function calling / tools | 当前 REST 聊天处理器未实现工具调用协议，不用于自动执行工具的 Agent |
| Responses API | 未提供 `/v1/responses`，客户端必须选择 Chat Completions |
| Embeddings、语音等其他 OpenAI 接口 | 当前服务没有提供 |
| temperature / max_tokens | 不控制网页生成；不要依赖这些参数生效 |
| JSON 强制输出 | 没有结构化输出约束；可以文字要求 JSON，但调用方必须解析、校验 |
| Token 用量 | usage 返回 0，不是真实计费或用量统计 |

这是一层操作 ChatGPT 网页的接口，兼容的是部分请求和响应格式。模型列表能返回、健康检查正常，都不能代替实际聊天验收。`auto` 使用网页默认模型；指定其他模型时，选模失败可能继续使用网页当前模型，返回的 model 字段不是实际模型切换成功的证明。

## 3. 最小请求与成功标准

```json
{
  "model": "auto",
  "messages": [
    {"role": "system", "content": "这是一个独立的文本测试任务。只回答本次问题。"},
    {"role": "user", "content": "Reply with exactly: OK"}
  ],
  "stream": false
}
```

发送到完整聊天 URL，并带上 `Authorization` 和 `Content-Type: application/json`。

成功响应的关键字段示意：

```json
{
  "object": "chat.completion",
  "model": "auto",
  "conversation_id": "网页会话ID",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "OK"},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

业务代码读取 `choices[0].message.content`。首次验收要求 HTTP 200 且正文去除首尾空白后为 `OK`；空内容应当作失败处理。`conversation_id` 是本项目扩展字段，可能为空；不要把顶层 `id` 的 `chatcmpl-...` 值当成网页会话 ID。

## 4. 可复制的调用示例

### Windows PowerShell：不安装 SDK

在运行调用项目的电脑上执行。密钥提示框中粘贴实际 API Key。

```powershell
$nasKeySecure = Read-Host '粘贴 NAS API Key' -AsSecureString
$nasApiKey = [System.Net.NetworkCredential]::new('', $nasKeySecure).Password
$nasHeaders = @{ Authorization = "Bearer $nasApiKey" }
$nasBody = @{
    model = 'auto'
    stream = $false
    messages = @(
        @{ role = 'system'; content = '这是独立测试，只回答本次问题。' }
        @{ role = 'user'; content = 'Reply with exactly: OK' }
    )
} | ConvertTo-Json -Depth 8
$nasReply = Invoke-RestMethod `
    -Uri 'http://192.168.1.100:11111/v1/chat/completions' `
    -Method Post -Headers $nasHeaders -ContentType 'application/json; charset=utf-8' `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($nasBody)) -TimeoutSec 180
$nasReply.choices[0].message.content
```

只执行一次，等它完成。发生超时后先看网页和日志，不要连续重发。

### Python：标准库，无第三方依赖

先在调用项目进程环境中设置 `NAS_CHATGPT_API_KEY` 为真实密钥。以下脚本不会读取 NAS 共享文件，也不会自动重试。显式绕过系统 HTTP 代理，直连局域网 NAS。

```python
import json
import os
import urllib.error
import urllib.request

base_url = "http://192.168.1.100:11111/v1"
api_key = os.environ["NAS_CHATGPT_API_KEY"].strip()
payload = {
    "model": "auto",
    "stream": False,
    "messages": [
        {"role": "system", "content": "这是独立任务，只处理本次输入。"},
        {"role": "user", "content": "Reply with exactly: OK"},
    ],
}
req = urllib.request.Request(
    base_url + "/chat/completions",
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    },
    method="POST",
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(req, timeout=180) as response:
        result = json.load(response)
except urllib.error.HTTPError as exc:
    # 调试时在自己的终端查看；不要把原始错误/提示词直接写到公开日志。
    raise RuntimeError(
        f"NAS API HTTP {exc.code}: " + exc.read().decode("utf-8", errors="replace")
    ) from exc

choices = result.get("choices") or []
text = choices[0].get("message", {}).get("content") if choices else None
if not isinstance(text, str) or not text.strip():
    raise RuntimeError("API 返回空内容，请查看 NAS 网页与日志")
print(text)
if text.strip() != "OK":
    raise RuntimeError("接口有回复，但未通过本次 OK 验收")
```

### 已使用 OpenAI Python SDK 的项目

以下是接入配置示例，SDK 版本以调用项目现有依赖为准；本次未运行 SDK 示例。环境变量中的密钥仍使用 `NAS_CHATGPT_API_KEY`。

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="http://192.168.1.100:11111/v1",
    api_key=os.environ["NAS_CHATGPT_API_KEY"],
    timeout=180.0,
    max_retries=0,
)
reply = client.chat.completions.create(
    model="auto",
    stream=False,
    messages=[
        {"role": "system", "content": "这是独立任务，只处理本次输入。"},
        {"role": "user", "content": "Reply with exactly: OK"},
    ],
)
text = reply.choices[0].message.content
if not text or not text.strip():
    raise RuntimeError("NAS API 返回空内容")
print(text)
```

如果调用项目使用全局代理，应让 NAS 地址绕过代理，例如在该进程的 `NO_PROXY` 中加入 `192.168.1.100`，保留原有条目。

## 5. 独立任务与连续对话：务必区分

给后端接入的完整流程、存储字段、异常处理和可运行示例，见[后端会话接入说明](docs/CONVERSATION-API.md)。

### A. 独立任务：推荐初次接入使用

在请求顶层设置 **`new_conversation: true`，不传 `conversation_id`**，即可明确开启新网页聊天，文字和图片都适用，不必为了新建而特意添加 system。原有“带 system、不传 ID”的写法仍然兼容。

```json
{
  "model": "auto",
  "new_conversation": true,
  "messages": [{"role": "user", "content": "这是新的独立任务。"}],
  "stream": false
}
```

参数必须是 JSON 布尔值，不能写字符串 `"true"`。同时传 `true` 和非空 `conversation_id` 会在操作浏览器之前返回 HTTP 400。省略或传 `false` 时，有 ID 就续聊；无 ID 在池模式下始终新建，单路模式保留旧选择行为。明确指定的 ID 无法访问时返回错误，不会自动另开会话。这个参数不会修改账号层面的设置。

system 消息实际会被拼成 `[System Instructions]` 文本放入网页输入框，不具有官方 API 独立 system 通道的语义和权限。

### B. 调用方管理历史

每次设置 `new_conversation: true`，并按顺序发送需要保留的 user/assistant 历史，不传 conversation_id。服务会开新网页聊天，将这些历史拼成文本提交。当前代码最多保留最后 20 条 user/assistant 消息，并非无限上下文；长内容应由调用方摘要或裁剪。

### C. 沿用已有网页会话

第一次按 A 发起，保存响应的非空 `conversation_id`。后续显式传这个 ID，并只发送本轮新增的 user 内容：

```json
{
  "model": "auto",
  "conversation_id": "上一次响应的真实conversation_id",
  "messages": [{"role": "user", "content": "继续解释上一个回答。"}],
  "stream": false
}
```

SDK 新建时传 `extra_body={"new_conversation": True}`，续聊时传 `extra_body={"conversation_id": "..."}`。ID 必须来自当前账号实际可访问的 ChatGPT 会话，不能自行编造。非流式响应在顶层返回 ID，成功的 SSE 在最后的 stop 事件中返回；能否方便读取取决于客户端封装，通用接入可直接使用 HTTP JSON。

不要同时提交整段历史又继续原网页会话，否则历史会重复。不同业务会话必须保存各自的 ID。启用 REST 池时，无 `conversation_id` 始终新建；单路旧模式才可能自动沿用上一次聊天。无论哪种模式，都应明确新建或续聊，不依赖共享的“上次会话”。

初次验收可串行调用；会话 ID 管理正确后，不同会话可按 `/health.rest_pool.size` 并发，同一业务会话仍须等待上一轮结束。请求进行时不要在 noVNC 中手动切换网页会话或发送消息，以免干扰自动操作。

## 6. 文档处理的可行方式

另一个项目如果想处理文件，可以自己先读取 TXT、Markdown，或用已有解析库从 PDF、DOCX、XLSX 提取文字，再发送：

```json
{
  "model": "auto",
  "stream": false,
  "messages": [
    {"role": "system", "content": "根据提供的文档文字总结，不确定的内容明确说明。"},
    {"role": "user", "content": "请总结以下文档：\n\n这里放调用方提取的文档文字。"}
  ]
}
```

这不是上传原文档：版式、表格结构可能丢失。单纯发送本地文件路径、NAS 路径或普通文本中的 base64 字符串，服务不会自动读取附件。图片需使用[图片接入说明](docs/IMAGE-INPUT.md)中的 image_url 格式。长文档先分段处理，不能把 HTTP 请求大小上限当作模型可用上下文上限。

## 7. 检查与错误处理

健康地址：`http://192.168.1.100:11111/health`。模型地址：`http://192.168.1.100:11111/v1/models`（携带 Bearer 密钥）。远程浏览器：`http://192.168.1.100:6080/vnc.html`。

健康检查重点看 `chrome_running`、`driver_connected`、`last_error`、`open_breakers`。模型列表可能来自回退列表；列表正常不能证明网页能发送消息。

| 现象 | 优先检查 |
|---|---|
| 连接拒绝或无法访问 | NAS IP、11111 端口、容器状态、调用项目网络与代理 |
| 404 | 是否错误调用 Responses，或 Base URL 重复拼接 /v1 |
| 400 | JSON 是否正确、messages 是否包含 user 消息 |
| 401 + Invalid API key / auth_error | 本服务 API Key 是否正确 |
| 401 + 登录过期相关信息 / invalid_api_key | 也可能是 ChatGPT 网页登录失效，应查看网页，不能只换本地密钥 |
| 429 | 查看 Retry-After，等限流解除；服务内部可能已做过退避重试 |
| 503 | 查看错误 code：lock_timeout、circuit_open 等；先处理排队、熔断或浏览器状态 |
| 504 / reply_timeout | 等待完整回复超时；可以放弃旧尝试并按新会话策略重试一次 |
| 504 / image_upload_timeout | 上传未确认（`prompt_sent=false`）；查看脱敏的上传阶段、状态和网页 |
| 504 / generation_stuck | 生成停滞；检查网页是否卡住，以及代理网络 |
| 500 或客户端超时 | 消息可能已发出；可退避后放弃旧尝试、在新会话重试一次，旧结果必须丢弃 |
| HTTP 200 但 content 为空 | 按失败处理，保留诊断信息，不作为成功结果交给业务 |
| 网页已有答案但 API 没拿到 | 属于回复采集问题，不代表 Google 账号必须重新登录 |

NAS SSH 中执行以下命令可以看日志，不是在 Windows PowerShell 中执行：

```sh
sudo docker logs --tail 150 chatgpt-web2api
```

在 NAS 的项目目录执行，只检查、不发送聊天：

```sh
sudo docker compose -f compose.yaml -f compose.headed.yaml exec -T chatgpt-web2api python - < scripts/check-nas-api.py
```

需要主动验收时，将 `python - <` 改为 `python - --send <`，会真的发送一条测试消息；不要把参数放在输入重定向的文件名之后。

流式模式下，HTTP 200 已开始后仍可能出现 `finish_reason: "error"` 和 `[Error: ...]`。之后即使收到 `[DONE]` 也不能视为成功；这也是首次联调优先使用非流式的原因。

## 8. 可直接交给另一个项目的接入要求

复制下面这段给负责另一个项目的开发者或编程助手，再在该项目的私有环境配置中填入密钥：

```text
请给本项目接入我 NAS 上现有的 ChatGPT-Web2API，先做最小文本联调。

Base URL：http://192.168.1.100:11111/v1
Endpoint：POST /chat/completions（相对于上述 Base URL）
模型：auto
鉴权：Authorization: Bearer <从环境变量 NAS_CHATGPT_API_KEY 读取>

要求：
1. 使用 Chat Completions，不使用 Responses API。
2. 首次设置 stream=false、并发1、客户端超时180秒，关闭普通 HTTP/SDK 原样重试。网关的新会话重试按[完整约定](docs/NEW-CONVERSATION-RETRY.md)实现。
3. 每个独立任务设置 new_conversation=true、不传 conversation_id，避免沿用共享的上次会话。
4. 输入先只传字符串文本；需要图片时遵循 Base64 image_url 约定，不假定原文档附件、tools 或 response_format 生效。
5. 从 choices[0].message.content 读取回复；空内容按失败处理。
6. 先只发一次“Reply with exactly: OK”，校验 HTTP200 和正文OK，再接入业务。
7. 超时或 5xx 可按新会话策略自动重试一次，必须隔离旧结果并重建输入；401 先区分本地密钥错误和网页登录过期。
8. 密钥只放服务端环境配置，不写入源码或前端，不提交仓库。
9. 如需处理原文档，在调用项目中先提取文字；图片按单独的图片接入说明上传。
10. 保留现有服务配置，不修改 NAS 的 Docker、Chrome profile、Cookie 和代理。

完成后报告：修改的配置/代码、一次测试的结果、仍有哪些不兼容功能。
```

## 9. 回复完整性与读取模式

原有算法按网页文字长度拼接增量，网页改写前缀时可能丢字或重复。修复版整体返回核对后的最终正文，不猜测修复 JSON 或业务 ID。

| 模式 | 设置与行为 |
|---|---|
| 默认核对模式 | `W2A_REPLY_SOURCE=reconciled`；完成检测后核对本轮完整回复，严格条件下允许 fresh WEB 新会话的稳定页面回退 |
| 协议读取模式 | `W2A_REPLY_SOURCE=backend`；浏览器负责登录和发送，直接从会话接口读取本轮完整正文及完成状态，不抓取回答 DOM，不允许页面回退 |

这是服务端配置，不是每个 API 请求中的参数。公开配置默认 reconciled；本次 NAS 合成测试验收使用 backend。backend 模式要求正式会话 ID 和可信的本轮匹配，无法确认、空白或超时都明确失败。默认模式的页面回退可读取孤立代码块正文，但拒绝混合正文/代码及多代码块场景的页面回退。

`stream: true` 仍为 SSE，正文在核对完成后发送。客户端和反向代理必须允许等待完整生成；收到错误结束事件不能当作成功。模型本身仍可能输出非法 JSON 或错误业务结论。

## 10. 本手册的验证范围

接口字段、会话分支和错误码已对照源码。此前回复完整性与协议模式通过 146 项相关离线回归和本机合成 JSON 测试；此前图片、会话控制与启动检查通过 145 项相关测试；2026-09-25 的 REST 并发与图片兼容更新通过 221 项相关离线测试，接入要求见[并发迁移说明](docs/REST-CONCURRENCY.md)。NAS 实测使用合成图片与虚构编号，覆盖新建、识图、SSE 追问和切回旧会话，详见[图片验证记录](docs/IMAGE-INPUT.md)。

后续新会话重试提示更新通过 235 项相关离线测试，其中新增 14 项重试用例。部署后已实际检查 HTTP 400/401 的策略字段及两路健康状态，该组检查未发送聊天。网关预算持久化、迟到事件过滤和前端新 ID 回填仍需调用项目验收，见[重试验证范围](docs/NEW-CONVERSATION-RETRY.md#4-验收)。

长文本、复杂图片、高并发和长期稳定性尚未全面验收，业务语义仍需调用方审查。示例需在自己的网络和依赖环境中验证；SDK 示例未单独执行。公开文档不包含下游项目的实际任务或数据。
