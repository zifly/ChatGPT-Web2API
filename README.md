# ChatGPT-Web2API — NAS deployment and reply integrity fork

Based on [Octo-Lex/ChatGPT-Web2API](https://github.com/Octo-Lex/ChatGPT-Web2API), baseline `497527dceabfa3f95961e23c291e618c5570f1ac`. The original MIT license and attribution are retained. This is an unofficial community project, not the official OpenAI API.

本 Fork 在上游网页转 API/MCP 功能上，补充 NAS 部署、远程桌面和回复完整性修复。浏览器仍负责登录与发送；新增模式直接从网页会话接口读取本轮完整回复，不是完全脱离浏览器的纯 HTTP 客户端。

## Documentation / 使用文档

| Guide | 中文 | English |
|---|---|---|
| Installation, login and troubleshooting | [NAS 安装与排查](NAS安装与故障排查手册.md) | [NAS installation and troubleshooting](NAS-INSTALLATION.md) |
| API setup, examples and limitations | [API 使用与接入](API使用与项目接入手册.md) | [API usage and integration](API-USAGE.md) |

## What this fork changes / 修改内容

The core API/MCP implementation, browser automation and turn-correlation machinery come from upstream. This fork adds:

- **NAS deployment:** signed Google Chrome package keyring, explicit base/desktop images, persistent private data and configurable LAN binding. Both API and desktop ports default to loopback.
- **Browser desktop:** Xvfb, x11vnc and noVNC with a password file. Port `6080/` opens the desktop client directly; `/vnc.html` remains supported. Desktop login can wait indefinitely, and startup handles stale Xvfb locks.
- **Optional network settings:** official Debian/PyPI sources and no proxy by default. Build mirrors, build proxy and runtime proxy are separate options; mainland-China examples are optional.
- **Cookie compatibility:** UTF-8 BOM and SameSite normalization, with CDP import acknowledgments checked.
- **Conversation handling:** temporary `WEB:` IDs are not treated as server-issued conversation IDs; a guarded fresh-chat DOM fallback is available in the default reconciled mode.
- **Reply integrity:** provisional DOM deltas are no longer exposed to API consumers. Final text replaces the entire provisional answer instead of merely adding a suffix. This addresses corruption when the webpage rewrites earlier characters during rendering.
- **Backend-only reply mode:** `W2A_REPLY_SOURCE=backend` skips assistant DOM text and DOM completion detection. It polls the authenticated webpage conversation endpoint and returns only a completed reply matched to the current turn. Unresolved IDs, ambiguous/partial replies and deadlines fail explicitly; this mode never falls back to page text.

我们修复的是采集层的丢字、重复和错误拼接，不是通过补括号或猜测 ID 修复 JSON。模型本身仍可能生成格式不合要求或语义错误的内容，调用方需要校验。

## Quick start / 快速开始

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

## Validation / 验证范围（2026-09-20）

- **146 related offline regression tests passed** for the combined integrity fix and backend-only reader.
- A real isolated Chrome instance fetched a synthetic local conversation endpoint; protocol extraction preserved JSON, Unicode and whitespace while ignoring intentionally incorrect page text.
- One isolated Docker Desktop request using backend mode returned a synthetic JSON document exactly as requested (about 9 seconds).
- After deploying backend mode on one amd64 NAS, **six real business requests covering ten work-group results** passed JSON, schema, case-ID and candidate-pool checks. The second set of three requests was triggered through the actual review page, including progress and result display. Results remained pending review; formal music-library associations were not modified.
- This was three fixed samples exercised in two rounds, not six independent datasets. No independent same-turn protocol-original/API-text comparison was captured for those business requests. Valid JSON and valid IDs alone do not prove universal verbatim integrity.
- The review page was tested; the separate main-library page stopped at its administrator login, so its authenticated result display was not accepted. One excerpt-matching decision remained a business/prompt issue, separate from response corruption.

真实业务两轮测试通过，但长文本、多轮、高并发、长期稳定性以及 NAS 上的 SSE 路径尚未充分验收。上述结论不代表模型判断全部正确，也不代表所有场景都已验证。

An earlier clean Docker Desktop installation passed using optional TUNA mirrors and a proxy, including manual login and restart. The official-source path encountered download failures and did not complete the same clean-install acceptance. ARM compatibility is not claimed.

## Limits

The REST interface currently accepts text, not image or original-file uploads. It does not implement Responses API, tool calling or enforced structured output. Reported `usage` values are placeholders. Internal retries elsewhere may still occur even when client retries are disabled. Account limits continue to apply.

[Detailed fork changes](docs/NAS-FORK-CHANGES.md) · [Backend reply mode](docs/PROTOCOL-REPLY-EXPERIMENT.md) · [Upstream README](https://github.com/Octo-Lex/ChatGPT-Web2API#readme) · [MIT license](LICENSE)
