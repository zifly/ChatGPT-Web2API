# ChatGPT-Web2API 使用与项目接入手册

更新日期：2026-09-19。适用于本 NAS Fork。192.168.1.100、NAS-HOST 都是示例，需替换。本文面向调用接口的项目；安装和修复见同目录《NAS安装与故障排查手册.md》。

## 1. 接入参数

| 配置项 | 填写值 |
|---|---|
| 服务类型 | OpenAI Compatible / OpenAI 兼容 / 自定义 OpenAI |
| Base URL | `http://192.168.1.100:11111/v1` |
| 完整聊天 URL | `http://192.168.1.100:11111/v1/chat/completions` |
| 模型 | `auto` |
| API Key | 从 NAS 项目的 `data/api.env` 中读取 `W2A_API_KEYS` 的值 |
| 请求头 | `Authorization: Bearer <API Key>` |
| 初次接入 | `stream: false`，并发 1，关闭自动重试 |
| 建议客户端超时 | 180 秒；这是客户端设置，不会修改服务端超时 |

如果配置框要求 Base URL，填到 `/v1`；如果要求完整接口地址，填到 `/v1/chat/completions`。避免拼接成 `/v1/v1/chat/completions`。

`data/api.env` 的 Windows 路径是 `\\NAS-HOST\docker\chatgpt-web2api\data\api.env`。只复制等号后的密钥值，不复制变量名；多个密钥用逗号分隔时，选其中一个。它是本服务的访问密钥，不是官方 OpenAI API Key，也不是 Google 密码。手册不包含真实密钥。

调用项目必须能访问 NAS 局域网。另一个 Docker 容器也用 NAS IP 和 11111 端口，不能用 `localhost` 指向 NAS。云端部署的项目通常不能直接访问 `192.168.1.100`，需要另行配置可达的私有网络。

## 2. 能做什么，不能做什么

| 功能 | 当前情况 |
|---|---|
| 普通文本聊天、总结、改写、翻译 | 已实测文本非流式请求返回 `OK` |
| 多轮文本 | 代码支持历史 messages 和自定义 conversation_id；用法见第 5 节 |
| 流式 SSE | 代码支持，当前 NAS 尚未单独验收，接入先关闭 |
| 图片理解、图片附件 | 当前 API 不支持；image_url 内容不会被上传 |
| PDF、Word、Excel 等文件上传 | 当前 API 不支持，无 `/v1/files` 上传端点 |
| 读取文档内容 | 调用方先提取文字，再作为普通文本发送；图片、扫描 PDF 需调用方另做 OCR |
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

### A. 独立任务：推荐初次接入使用

每次请求都携带一条 `system` 消息，并且**不传 conversation_id**。当前实现中，这样会走新建网页聊天的分支，避免自动继续上一段会话。示例已经采用这个写法。

system 消息实际会被拼成 `[System Instructions]` 文本放入网页输入框，不具有官方 API 独立 system 通道的语义和权限。

### B. 调用方管理历史

每次携带 system，并按顺序发送需要保留的 user/assistant 历史，不传 conversation_id。服务会开新网页聊天，将这些历史拼成文本提交。当前代码最多保留最后 20 条 user/assistant 消息，并非无限上下文；长内容应由调用方摘要或裁剪。

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

SDK 可通过 `extra_body={"conversation_id": "..."}` 传扩展字段；能否方便读取该字段取决于客户端封装，通用接入可直接使用 HTTP JSON。

不要同时提交整段历史又继续原网页会话，否则历史会重复。不同业务会话必须保存各自的 ID。当前代码在没有 conversation_id、没有 system 且符合条件时，会自动沿用服务上次的聊天；不能把“未传 ID”理解成“一定新建会话”。不要依赖这个共享状态做多用户隔离。

初次接入仅串行调用。请求进行时不要在 noVNC 中手动切换网页会话或发送消息，以免干扰自动操作。

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

这不是上传原文件：图片、版式、表格结构可能丢失。单纯发送本地文件路径、NAS 路径或 base64 字符串，服务不会自动读取附件。长文档先分段处理，不能把 HTTP 请求大小上限当作模型可用上下文上限。

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
| 504 / generation_stuck | 生成停滞；检查网页是否卡住，以及代理网络 |
| 500 或客户端超时 | 查看日志和网页，消息可能已发出；不要立即自动重发 |
| HTTP 200 但 content 为空 | 按失败处理，保留诊断信息，不作为成功结果交给业务 |
| 网页已有答案但 API 没拿到 | 属于回复采集问题，不代表 Google 账号必须重新登录 |

NAS SSH 中执行以下命令可以看日志，不是在 Windows PowerShell 中执行：

```sh
sudo docker logs --tail 150 chatgpt-web2api
```

在 NAS 的项目目录执行，只检查、不发送聊天：

```sh
sudo docker compose -f compose.yaml -f compose.headed.yaml exec -T chatgpt-web2api python scripts/check-nas-api.py
```

需要主动验收时，在命令末尾加 `--send`，会真的发送一条测试消息。

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
2. 首次设置 stream=false、并发1、客户端超时180秒，关闭自动重试。
3. 每个独立任务都带 system 消息、不传 conversation_id，避免沿用共享的上次会话。
4. 输入先只传字符串文本；不发送 image_url、文件附件、tools 或 response_format 并假定它们生效。
5. 从 choices[0].message.content 读取回复；空内容按失败处理。
6. 先只发一次“Reply with exactly: OK”，校验 HTTP200 和正文OK，再接入业务。
7. 若超时，先查看 NAS 网页与日志，不能无限重试；若401，区分本地密钥错误和网页登录过期。
8. 密钥只放服务端环境配置，不写入源码或前端，不提交仓库。
9. 如需处理文件，在调用项目中先提取文字；这个接口不支持上传图片或原文件。
10. 保留现有服务配置，不修改 NAS 的 Docker、Chrome profile、Cookie 和代理。

完成后报告：修改的配置/代码、一次测试的结果、仍有哪些不兼容功能。
```

## 9. 本手册的验证范围

接口字段、会话分支和错误码已对照本地 `src/chatgpt_web2api/api_server.py` 核对。历史 NAS 验收已通过 auto + 非流式文本返回 OK；本次编写手册没有额外发送聊天，也没有重启或修改服务。示例需要由调用项目在其网络和依赖环境下完成最终联调。
