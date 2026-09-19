# ChatGPT-Web2API — NAS deployment fork

Based on [Octo-Lex/ChatGPT-Web2API](https://github.com/Octo-Lex/ChatGPT-Web2API), baseline `497527dceabfa3f95961e23c291e618c5570f1ac`. Original MIT license and attribution retained.

NAS-focused Docker/noVNC setup, cookie import compatibility fixes, and guarded response retrieval fixes. This is an unofficial community fork, not an official OpenAI API service.

## Changes / 本 Fork 的改动

- Replace deprecated apt-key with a signed Chrome keyring.
- Add headed Chrome with Xvfb/noVNC and password authentication.
- Configure private persistent data, an optional proxy and explicit LAN binding.
- Normalize exported cookies and verify CDP import acknowledgements.
- Filter temporary WEB: conversation IDs before backend queries.
- Add a guarded fresh-chat reply fallback and regression tests.
- Provide Chinese deployment, troubleshooting and API integration guides.

核心网页转 API、MCP、流式和限流机制来自上游。本 Fork 聚焦 NAS 部署与特定故障修复。

## Getting started / 开始使用

Read [中文安装与排查手册](NAS安装与故障排查手册.md), then [API 接入手册](API使用与项目接入手册.md).
Create private data/api.env and data/vnc-password.txt. Set your NAS LAN IP in .env, then run:

```sh
sudo sh scripts/enable-nas-desktop.sh
```

The script builds both images. Listeners default to loopback until W2A_BIND_ADDRESS is explicitly configured.
Official Debian/PyPI sources and no proxy are the defaults. Optional mainland-China mirror/proxy examples are in the Chinese setup guide and `.env.example`; each setting is independent. Desktop mode waits for manual login without a five-minute timeout by default, and handles stale Xvfb locks on restart.

No keys, cookies, Chrome profiles, VNC passwords or runtime logs are distributed.

## Validation / 验证范围

Non-streaming text with model auto was verified on one amd64 NAS. An independent Linux/amd64 Docker Desktop installation was also built and started with optional TUNA mirrors and a proxy; after manual login, one non-streaming API test returned OK. Desktop restart was verified. The official-source path previously encountered network download failures and has not completed the same fresh-install acceptance. No claim of ARM support, universal compatibility or continuous availability.

Images and original file uploads are not supported by this REST API. Streaming and multi-client operation have not been accepted on this NAS. The guarded DOM fallback has regression tests but has not been separately verified live.

Internal retries may occur even if client retries are disabled. ChatGPT account limits and service terms still apply. Keep account credentials and the logged-in desktop private.

## Upstream and license

[Upstream README](https://github.com/Octo-Lex/ChatGPT-Web2API#readme) · [Fork changes](docs/NAS-FORK-CHANGES.md) · [MIT license](LICENSE)
