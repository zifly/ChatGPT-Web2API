# chatgpt-web2api-nas — NAS deployment and reply integrity fork

Based on [Octo-Lex/ChatGPT-Web2API](https://github.com/Octo-Lex/ChatGPT-Web2API), baseline `497527dceabfa3f95961e23c291e618c5570f1ac`. The original MIT license and attribution are retained. This is an unofficial community project, not the official OpenAI API.

本 Fork 在上游网页转 API/MCP 功能上，补充 NAS 部署、远程桌面和回复完整性修复。浏览器仍负责登录与发送；新增模式直接从网页会话接口读取本轮完整回复，不是完全脱离浏览器的纯 HTTP 客户端。

## Documentation / 使用文档

| Guide | 中文 | English |
|---|---|---|
| Pull a prebuilt Docker image | [直接拉取镜像](docs/PREBUILT-IMAGE.md) | [Prebuilt image (bilingual)](docs/PREBUILT-IMAGE.md#english) |
| Installation, login and troubleshooting | [NAS 安装与排查](NAS安装与故障排查手册.md) | [NAS installation and troubleshooting](NAS-INSTALLATION.md) |
| API setup, examples and limitations | [API 使用与接入](API使用与项目接入手册.md) | [API usage and integration](API-USAGE.md) |
| Backend-managed conversations | [后端会话接入](docs/CONVERSATION-API.md) | [Conversation integration (bilingual)](docs/CONVERSATION-API.md) |
| Web citation sources | [引用来源与显示](docs/CITATIONS.md#中文) | [Citation sources and rendering](docs/CITATIONS.md#english) |
| Timeouts and browser recovery | [超时与恢复](docs/REQUEST-RECOVERY.md#中文) | [Request deadlines and recovery](docs/REQUEST-RECOVERY.md#english) |
| Error codes and client handling | [错误码与后端处理](docs/ERROR-CODES.md) | [Error codes and client handling](docs/ERROR-CODES.en.md) |
| In-request preparation recovery | [请求内安全恢复](docs/SAFE-RECOVERY.md#中文) | [Safe recovery](docs/SAFE-RECOVERY.md#english) |

## What this fork changes / 修改内容

The core API/MCP implementation, browser automation and turn-correlation machinery come from upstream. This fork adds:

- **NAS deployment:** signed Google Chrome package keyring, explicit base/desktop images, persistent private data and configurable LAN binding. Both API and desktop ports default to loopback.
- **Browser desktop:** Xvfb, x11vnc and noVNC with a password file. Port `6080/` opens the desktop client directly; `/vnc.html` remains supported. Desktop login can wait indefinitely, and startup handles stale Xvfb locks.
- **Optional network settings:** official Debian/PyPI sources and no proxy by default. Build mirrors, build proxy and runtime proxy are separate options; mainland-China examples are optional.
- **Cookie compatibility:** UTF-8 BOM and SameSite normalization, with CDP import acknowledgments checked.
- **Client-controlled chats:** `new_conversation: true` explicitly starts a new chat; `conversation_id` continues a chosen chat. Conflicting controls return HTTP 400. Omitting both keeps the legacy behavior. See [API usage](API-USAGE.md#5-independent-tasks-and-continued-conversations).
- **Image input:** REST chat requests can upload PNG/JPEG/WebP Base64 images through the webpage and receive text answers. Upload completion is checked before sending. See the [bilingual image guide](docs/IMAGE-INPUT.md) for limits and examples.
- **Conversation handling:** temporary `WEB:` IDs are not treated as server-issued conversation IDs; a guarded fresh-chat DOM fallback is available in the default reconciled mode.
- **Reply integrity:** provisional DOM deltas are no longer exposed to API consumers. Final text replaces the entire provisional answer instead of merely adding a suffix. This addresses corruption when the webpage rewrites earlier characters during rendering.
- **Web citation sources:** supported source mappings from the verified reply are returned as `message.annotations` (`delta.annotations` for SSE), preserving the original text. Clients render links using reference IDs, titles and URLs. Missing or ambiguous sources stay unresolved; previously saved replies are not repaired automatically. See [format and limitations](docs/CITATIONS.md).
- **Timeout recovery:** transient read timeouts stay within the original reply deadline and do not replay the send. Upload timeouts include sanitized phase/state diagnostics; non-streaming errors distinguish `image_upload_timeout` (not sent) from `reply_timeout` (inspect the submission state).
- **Request deadline and browser pause:** REST queueing, navigation, upload and reply reading share one deadline. Errors distinguish confirmed, absent and uncertain submissions. Repeated CDP timeouts pause browser operations; an authenticated read-only recovery probe can unpause them without replaying a request. See [recovery behavior](docs/REQUEST-RECOVERY.md).
- **Backend-only reply mode:** `W2A_REPLY_SOURCE=backend` skips assistant DOM text and DOM completion detection. It polls the authenticated webpage conversation endpoint and returns only a completed reply matched to the current turn. Unresolved IDs, ambiguous/partial replies and deadlines fail explicitly; this mode never falls back to page text.

我们修复的是采集层的丢字、重复和错误拼接，不是通过补括号或猜测 ID 修复 JSON。模型本身仍可能生成格式不合要求或语义错误的内容，调用方需要校验。

网页引用会随回复返回编号、标题和网址，需要调用项目的前端显示为链接。没有可靠对应关系时不会猜测网址，旧回复也不会自动补齐来源。引用功能已通过离线测试，真实 NAS 网页验收仍待完成。

## Quick start / 快速开始

For installation without building on the NAS, use the [prebuilt image guide](docs/PREBUILT-IMAGE.md) and standalone `compose.image.yaml`. 镜像包含浏览器与远程桌面，直接拉取即可；仍需配置私有密码并登录自己的 ChatGPT 账号。The source-build workflow below remains available.

Follow the installation guide to create `.env`, `data/api.env` and `data/vnc-password.txt`. For the protocol-reading configuration tested on the NAS, set these in `.env` (replace the example IP):

```dotenv
W2A_BIND_ADDRESS=192.168.1.100
W2A_REPLY_SOURCE=backend
```

Then run from the project directory on the NAS:

```sh
sudo sh scripts/enable-nas-desktop.sh
```

Open `http://192.168.1.100:6080/` and log into ChatGPT. API base URL: `http://192.168.1.100:11111/v1`.

`backend` is opt-in; the code/Compose default is `reconciled`. Both modes retain the browser for login, model selection and sending. Both return verified complete text; SSE still uses SSE framing but buffers content until completion instead of delivering live tokens.

默认仍为 `reconciled`；要使用本次 NAS 验收的协议读取方式，需明确设置 `W2A_REPLY_SOURCE=backend`。切换配置需要重建或重新创建相应容器，修改文件不会自动更新运行中的进程。

No API keys, cookies, Chrome profiles, VNC passwords or runtime logs are distributed. Keep `data/` and `.env` private.

## Validation / 验证范围（2026-09-22）

- **146 related offline regression tests passed** for the combined integrity fix and backend-only reader.
- A real isolated Chrome instance fetched a synthetic local conversation endpoint; protocol extraction preserved JSON, Unicode and whitespace while ignoring intentionally incorrect page text.
- One isolated Docker Desktop request using backend mode returned a synthetic JSON document exactly as requested (about 9 seconds).
- Current image-input, conversation-control and startup-check coverage comprises 153 related offline tests. Synthetic NAS checks passed for a fresh text chat, a fresh two-image chat, an SSE follow-up and switching back to the first conversation. See [image validation](docs/IMAGE-INPUT.md).
- The citation update passed 184 related offline tests, including actual JavaScript projection execution, exact reply text, per-turn source isolation, JSON/SSE annotations and existing text/image paths. Live NAS citation acceptance remains pending; this is not a claim that every webpage citation format is supported.
- The current citation, request-guard and safe-navigation-recovery code passed **352 related offline tests**, including deadlines/disconnection, uncertain clicks, queue cancellation, browser pause, bounded read-only recovery and JSON/SSE diagnostics. Four sequential synthetic NAS requests passed: new text chat, explicit-ID text continuation, image recognition and image-context continuation. Each client made one POST; diagnostics were valid and final health was healthy. All recovery counts were zero: normal-path compatibility passed live acceptance, while recovery branches remain verified by offline fault injection rather than a live fault.
- These small synthetic checks do not establish broad model accuracy, long-context behavior or concurrency guarantees.

公开验收示例仅使用合成图片、通用文字和虚构测试编号，不包含下游项目的实际任务、数据或界面。长文本、高并发和长期稳定性尚未充分验收；模型判断仍需调用方验证。

An earlier clean Docker Desktop installation passed using optional TUNA mirrors and a proxy, including manual login and restart. The official-source path encountered download failures and did not complete the same clean-install acceptance. ARM compatibility is not claimed.

## Limits

The REST interface accepts text and bounded Base64 image inputs; original document files are not supported. Image input and explicit conversation control passed synthetic acceptance on Docker Desktop and one amd64 NAS; see the image guide for scope. It does not implement Responses API, tool calling or enforced structured output. Reported `usage` values are placeholders. Internal retries elsewhere may still occur even when client retries are disabled. Account limits continue to apply.

[Detailed fork changes](docs/NAS-FORK-CHANGES.md) · [Backend reply mode](docs/PROTOCOL-REPLY-EXPERIMENT.md) · [Upstream README](https://github.com/Octo-Lex/ChatGPT-Web2API#readme) · [MIT license](LICENSE)
