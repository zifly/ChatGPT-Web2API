# NAS installation and troubleshooting

[中文](NAS安装与故障排查手册.md) · [README](README.md) · [API integration](API-USAGE.md)

Updated: 2026-09-20. Based on [Octo-Lex/ChatGPT-Web2API](https://github.com/Octo-Lex/ChatGPT-Web2API), with the original MIT license retained. Text requests and backend reply mode have been tested on one amd64 NAS; this is not a compatibility guarantee for every NAS. The Google Chrome image used here is amd64, not an ARM image.

`192.168.1.100` and `NAS-HOST` are examples. Replace them with your own NAS address. Run the shell commands below in the project directory in a NAS SSH terminal, not in Windows PowerShell.

## 1. Private configuration

```sh
mkdir -p data/chrome-profile data/cookies
cp .env.example .env
```

Set `W2A_BIND_ADDRESS` in `.env` to the NAS LAN IP to allow LAN access. The default binds published ports to `127.0.0.1` only.

Create `data/api.env` with your own long random service key:

```dotenv
W2A_API_KEYS=REPLACE_WITH_YOUR_OWN_RANDOM_API_KEY
```

Create `data/vnc-password.txt` containing your own VNC password. Traditional VNC uses only the first eight characters. Keep the desktop accessible only on a trusted network. The entire `data/` directory and `.env` contain private runtime configuration and must stay out of Git.

### Package mirrors and proxies

Defaults are the official Debian, Debian Security and PyPI sources, with no build or runtime proxy. Chrome is installed from Google's signed package repository.

`W2A_DEBIAN_MIRROR`, `W2A_DEBIAN_SECURITY_MIRROR` and `W2A_PIP_INDEX_URL` override package sources. Security updates continue to use the official source unless explicitly changed. Mirrors can lag behind their upstream source.

For mainland-China network environments, these are optional examples for your local `.env`:

```dotenv
W2A_DEBIAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
W2A_PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
# Optional security-update mirror:
# W2A_DEBIAN_SECURITY_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian-security
# Optional reachable HTTP proxy; replace the port:
# W2A_BUILD_PROXY_URL=http://host.docker.internal:3128
# W2A_PROXY_URL=http://host.docker.internal:3128
```

Mirrors and proxies are independent. Changing package sources does not configure connectivity to ChatGPT.

- `W2A_PROXY_URL` sets the runtime browser/service proxy.
- `W2A_BUILD_PROXY_URL` sets the proxy for build downloads.
- Container `127.0.0.1` refers to that container. For a proxy on the NAS host, use `host.docker.internal` with its actual HTTP port and allow connections from the container network. Do not confuse HTTP and SOCKS ports.
- Do not put proxy account passwords in build settings. Keep local settings out of the repository.
- If Docker fails while pulling the `FROM python:...` image, the build steps have not started. Check the Docker daemon/Desktop proxy; `W2A_BUILD_PROXY_URL` does not configure base-image pulls.

Mirror references: [TUNA Debian](https://mirrors.tuna.tsinghua.edu.cn/help/debian/) and [TUNA PyPI](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/).

### Reply source

To use the configuration exercised in the NAS synthetic tests, add:

```dotenv
W2A_REPLY_SOURCE=backend
```

The browser still logs in and sends the message. Reply completion and content come directly from the authenticated conversation endpoint, matched to the current turn. This mode never reads assistant DOM text or falls back to a rendered reply. Missing persistent conversation IDs, uncertain correlation, empty/partial replies and deadlines fail explicitly.

If unset, the default is `reconciled`: completion detection followed by final-text reconciliation, with a strictly guarded fresh-chat DOM fallback. Both modes buffer content until verification; SSE no longer delivers provisional DOM tokens. Recreate the container after changing environment settings; rebuild the image after updating code.

## 2. Build, start and log in

```sh
sudo sh scripts/enable-nas-desktop.sh
```

The script builds the base image and then the desktop image. On failure, inspect the terminal output and `desktop-build.log` locally.

Open `http://192.168.1.100:6080/`, enter the password from `data/vnc-password.txt`, and log into ChatGPT in the remote browser. Older images that show a directory listing can use `http://192.168.1.100:6080/vnc.html`.

Chrome browser-sync login and ChatGPT website login are separate. A Chrome sync warning does not by itself mean the ChatGPT session is invalid.

Desktop mode defaults to `W2A_LOGIN_TIMEOUT_SECONDS=0`, which waits indefinitely for manual login. Set a positive number of seconds in `.env` to use a finite wait. Before login completes, the API may not be ready; a working desktop is not proof that chat generation works.

Optionally place your Cookie-Editor JSON export at `data/cookies/cookies.json`. It is imported at startup if present; old cookies can overwrite newer session state. Do not share cookies or the Chrome profile, and do not let multiple Chrome instances use the same profile.

## 3. Read-only checks and one-message acceptance

Run from the NAS project directory:

```sh
sudo docker compose -f compose.yaml -f compose.headed.yaml exec -T chatgpt-web2api python - < scripts/check-nas-api.py
```

This passes the local script through standard input, so it works even when an older image does not contain `/app/scripts/check-nas-api.py`. It checks health and models without sending a chat message.

To explicitly send one real test message requesting `OK`:

```sh
sudo docker compose -f compose.yaml -f compose.headed.yaml exec -T chatgpt-web2api python - --send < scripts/check-nas-api.py
```

Do not run repeated sends when a request times out. The model may already have received it.

API base URL: `http://192.168.1.100:11111/v1`. Start with model `auto`, non-streaming, one request at a time. See [API usage and integration](API-USAGE.md). Health or HTTP 200 alone does not establish response correctness. Immediately after startup, `starting` can mean that no chat request has completed yet; also check browser/driver connectivity and open breakers.

## 4. Troubleshooting

| Symptom | What to check |
|---|---|
| `apt-key` error | Use this fork's updated Dockerfile |
| Chrome does not start | Container logs, profile permissions, another Chrome using the profile |
| Cannot reach ChatGPT | Proxy listener, HTTP versus SOCKS port, host/container network |
| Cookies do not work | Export format, expiry, actual website login state |
| Desktop root shows files | Use `/vnc.html`; newer images include a default homepage |
| Diagnostic script missing in image | Use the standard-input check above; no rebuild is required just for that check |
| Page answered but API failed | Check conversation ID and reply collection logs before sending again |
| HTTP 401 | Distinguish the local service key from expired website authentication |
| HTTP 429 | Respect `Retry-After`; do not retry indefinitely |
| HTTP 503/504 | Inspect lock waits, circuit breakers, generation stalls and network failures |
| Invalid JSON or wrong candidate IDs | Reject the result and keep local evidence; do not guess missing characters |

```sh
sudo docker logs --tail 150 chatgpt-web2api
sudo sh scripts/diagnose-nas-auth.sh
```

Logs may include prompts or account details. Inspect locally and redact before sharing. Do not repeatedly delete a profile as a troubleshooting shortcut.

## 5. Maintenance and updates

Always use both Compose files for a desktop deployment, to avoid switching to headless mode accidentally:

```sh
sudo docker compose -f compose.yaml -f compose.headed.yaml ps
sudo docker compose -f compose.yaml -f compose.headed.yaml logs --tail 100
sudo docker compose -f compose.yaml -f compose.headed.yaml stop
sudo docker compose -f compose.yaml -f compose.headed.yaml up -d --no-build
```

Before upgrading, back up the code, image and private data. Stop the service before copying the complete profile to avoid inconsistent browser databases. Backups contain credentials and must remain private.

After updating source, rerun the build/start script and perform acceptance checks. A healthy existing container does not automatically load changed source files. The deployment-specific rollback script used for the maintainer's NAS is not a general installation requirement.

## 6. Supported scope

The updated REST chat interface handles text and bounded Base64 image inputs, but not original document uploads. See [image input](docs/IMAGE-INPUT.md) for rebuild requirements and isolated Docker validation; synthetic NAS image and conversation-control acceptance is recorded in that guide. NAS SSE behavior, multi-client use and long-term stability have not been fully accepted. The guarded DOM fallback has regression coverage but no separate comprehensive NAS acceptance. Disabling client retries does not disable every retry inside the service.

## 7. Clean-install validation (2026-09-20)

A fresh public-repository clone was built on Windows Docker Desktop using Linux/amd64 containers, an empty Chrome profile and independent test credentials. No NAS login data was copied. With optional TUNA Debian/PyPI mirrors and separately configured build/runtime proxies, both images and the installation script succeeded.

Manual-login waiting and stale virtual-display locks were fixed. After restart, noVNC and Chrome CDP were reachable. Following manual login, the model list contained 21 entries and one `auto`, `stream: false` request returned `OK`.

The official-source attempt encountered network download failures and did not complete the same acceptance. These results do not establish compatibility with other hosts, ARM, streaming, concurrent clients or long-running operation.

## 8. Reply protocol and synthetic validation (2026-09-20)

The earlier integrity fix/backend reader passed 146 related offline tests and an exact local synthetic JSON request. The current image, conversation-control and startup-check suite passed 145 related tests. Synthetic inputs on one amd64 NAS verified fresh conversations, image recognition, SSE follow-up and explicit-ID switching. See [image validation](docs/IMAGE-INPUT.md).

These minimal synthetic checks do not establish broad vision accuracy, long-context behavior, concurrency or sustained stability. Consumers must validate output semantics and format. Public examples do not contain downstream application instances.
