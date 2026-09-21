# NAS 安装与故障排查手册（公开版）

[English](NAS-INSTALLATION.md) · [项目首页](README.md)

不想在 NAS 上编译，可按[直接拉取镜像指南](docs/PREBUILT-IMAGE.md)使用独立的 `compose.image.yaml`。两种方式使用相同的私有配置；本手册后面的构建命令仅适用于源码安装。

本 Fork 基于 https://github.com/Octo-Lex/ChatGPT-Web2API ，保留原 MIT 许可证。
已在一台 amd64 NAS 验证文本非流式及协议读取模式的真实业务请求，不是所有 NAS 的兼容承诺。
192.168.1.100 是示例地址，请替换为自己的 NAS 地址。AMD64 Chrome 镜像不能直接用于 ARM。

## 1. 私有配置

以下命令在 NAS SSH 的项目目录运行：

```sh
mkdir -p data/chrome-profile data/cookies
cp .env.example .env
```

编辑 `.env`，设置 `W2A_BIND_ADDRESS=自己的NAS局域网IP`。默认仅监听 127.0.0.1。
需要代理时设置 W2A_PROXY_URL，填真实的 HTTP 代理端口，不要混用 SOCKS 端口。
代理在 NAS 本机时可用 host.docker.internal，且代理应允许容器网络连接。

### API Key 是什么，如何生成？

这里的 API Key 是**部署者自己设置的 Web2API 服务访问密码**，不需要向 OpenAI 申请，也不从 ChatGPT Cookie 或 Google 账号中提取。它只决定谁能调用本服务；实际使用的 ChatGPT 账号由浏览器的登录状态决定。VNC 密码则用于打开远程桌面，是另一份凭据。

**已有部署直接使用原密钥，不要为了查看或接入再次生成。** 密钥保存在项目的 `data/api.env` 中；Docker Compose 启动时读取其中的 `W2A_API_KEYS`。镜像和仓库不提供统一的默认密钥，也不会在每次启动时生成新密钥。

仅首次安装时，可在 NAS 项目目录执行以下命令，生成 32 字节随机值（64 个十六进制字符），并写入私有文件。需要系统已有 `openssl`；如果文件已经存在，命令会拒绝覆盖：

```sh
mkdir -p data
(
    umask 077
    set -C
    service_key=$(openssl rand -hex 32) || exit 1
    printf 'W2A_API_KEYS=%s\n' "$service_key" > data/api.env
)
```

如果提示找不到 `openssl`，可以在装有 Python 3 的电脑上运行下面的命令，再手动将生成值写入 NAS 的 `data/api.env`：

```sh
python3 -c "import secrets; print(secrets.token_hex(32))"
```

文件格式如下，必须将占位符替换为自己生成的值：

```dotenv
W2A_API_KEYS=REPLACE_WITH_YOUR_OWN_RANDOM_API_KEY
```

客户端的 **API Key** 配置框只填等号后的值，不填 `W2A_API_KEYS=`，也不填 `Bearer `。请求头由客户端发送为 `Authorization: Bearer <密钥值>`。如果配置了逗号分隔的多个密钥，客户端选其中一个；这些密钥不提供独立用户或会话权限隔离。

需要更换密钥时，先等待在途任务完成，修改 `data/api.env`，再按你的部署方式选择下面**一条**命令重新创建容器（仅 `docker restart` 不会重新读取 Compose 的环境文件），并同步修改所有客户端保存的密钥：

```sh
# 拉取版
sudo docker compose -f compose.image.yaml up -d --no-build --force-recreate
# 源码构建版
sudo docker compose -f compose.yaml -f compose.headed.yaml up -d --no-build --force-recreate
```

不要将密钥、`data/api.env` 或完整 Compose 展开配置提交到仓库、贴到 Issue；也不要通过清空 `W2A_API_KEYS` 来排查，因为空密钥列表会关闭接口的密钥校验。

### 构建镜像源和代理

默认使用 Debian、Debian Security 和 PyPI 官方源，不启用构建或运行代理；Chrome 来自 Google 官方签名仓库。
可在 `.env` 中通过 `W2A_DEBIAN_MIRROR`、`W2A_DEBIAN_SECURITY_MIRROR`、`W2A_PIP_INDEX_URL` 更换镜像。
安全更新默认保持官方源。可选镜像可能存在同步延迟。

中国大陆网络可参考以下可选配置，按需写入本地 `.env`。镜像与代理是独立开关，换源不会自动解决 ChatGPT 的访问问题：

```dotenv
W2A_DEBIAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
W2A_PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
# 如需安全更新镜像，再取消下面一行注释：
# W2A_DEBIAN_SECURITY_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian-security
# 如需代理，填写你自己的可达 HTTP 代理地址：
# W2A_BUILD_PROXY_URL=http://host.docker.internal:3128
# W2A_PROXY_URL=http://host.docker.internal:3128
```

`W2A_PROXY_URL` 配置运行时浏览器/服务代理；构建下载需要另设 `W2A_BUILD_PROXY_URL`。
容器内的 127.0.0.1 不是宿主机。代理在宿主机时，可用 `http://host.docker.internal:实际HTTP端口`，并确认代理允许容器访问。
构建代理不要填写账号密码；这些配置留在本地 `.env`，不提交到仓库。

若失败发生在 `FROM python:...` 的 Docker Hub 拉取阶段，说明尚未运行构建步骤，需要检查 Docker 守护进程或 Docker Desktop 的代理。`W2A_BUILD_PROXY_URL` 不负责基础镜像拉取。

清华配置依据：[Debian 镜像说明](https://mirrors.tuna.tsinghua.edu.cn/help/debian/) · [PyPI 镜像说明](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/)。

创建 `data/vnc-password.txt`，写入自己的 VNC 密码。传统 VNC 只使用前 8 个字符，桌面仅限可信局域网。
整个 data 目录都是私有运行数据，不提交到 Git。

### 回复读取模式

在 `.env` 中设置 `W2A_REPLY_SOURCE=backend` 可启用本次 NAS 业务验收使用的模式：浏览器仍负责登录和发送，完成状态与完整正文直接从网页会话接口读取，并核对当前轮次。不读取回答 DOM，不在失败时退回页面抓取。正式会话 ID 长时间不可用、轮次不确定或超时会返回错误。

未设置时默认 `reconciled`，使用完成检测后核对完整回复，严格条件下允许新会话页面回退。两种模式均已停止向客户端转发临时 DOM 增量；SSE 正文会等待最终核对。切换后需重新创建容器，代码更新需重建镜像。

## 2. 构建启动

```sh
sudo sh scripts/enable-nas-desktop.sh
```

脚本先构建基础镜像再构建桌面镜像。构建失败时查看终端及本机 desktop-build.log。
打开 `http://192.168.1.100:6080/`（旧镜像可用 `/vnc.html`），输入 VNC 密码，在浏览器里完成 ChatGPT 登录。
Chrome 同步登录和 ChatGPT 网站登录不同；网站可用时不必因同步提示而退出账号。
桌面配置默认设置 `W2A_LOGIN_TIMEOUT_SECONDS=0`（可在 `.env` 改为正整数秒数），表示持续等待人工登录。登录前 API 尚未就绪；桌面页面可用不等于聊天 API 已就绪。

可选：将自己的 Cookie-Editor JSON 放到 `data/cookies/cookies.json`。启动时存在就会导入；旧 Cookie 可能覆盖新状态。
不要分享 Cookie 或 chrome-profile，也不要让多个 Chrome 共用同一个 profile。

<a id="login-recovery"></a>

### 重新登录与停用旧 Cookie

首次安装可以直接在 6080 远程浏览器中登录，不需要先准备 `cookies.json`。如果已经导入过 Cookie，后来改为网页登录，建议停用旧文件：入口脚本每次启动都会导入它，可能覆盖新登录状态。保留 `data/chrome-profile`，不要通过删除浏览器资料来刷新登录。

遇到网页登录失效、上游 `401 token_expired`，或重新登录后 API 仍使用旧凭据时，按以下顺序处理：

1. 暂停调用方的新任务，等待在途请求结束；发送结果不确定的请求先核对网页，不自动补发。
2. 在 NAS SSH 的项目目录执行下面的命令，将旧 Cookie 文件改名保留；没有该文件就跳过，已有同名备份则停止。备份仍包含登录凭据，不要公开。

```sh
sudo sh -eu <<'SH'
if [ -f data/cookies/cookies.json ]; then
    cookie_backup="data/cookies/cookies.json.disabled-$(date +%Y%m%dT%H%M%S)"
    test ! -e "$cookie_backup"
    mv data/cookies/cookies.json "$cookie_backup"
fi
SH
```

3. 打开 `http://NAS-HOST:6080/`（也可用 `/vnc.html`），输入 `data/vnc-password.txt` 中的 VNC 密码，在**远程浏览器的 ChatGPT 网站**重新登录。若仍显示旧账号状态，可在该网站退出后再登录；本机普通浏览器登录或 Chrome 同步登录不会替代这一步。
4. 登录完成后，按部署方式选择**一条**命令重启现有服务，让进程重新读取网页登录凭据。这不是更新镜像，也不需要重新生成 API Key；桌面会暂时断开，稍后重新连接即可。

```sh
# 拉取版
sudo docker compose -f compose.image.yaml restart chatgpt-web2api
# 源码构建版
sudo docker compose -f compose.yaml -f compose.headed.yaml restart chatgpt-web2api
```

旧版 Compose 可使用 `docker-compose`。上述重启适用于只更新网页登录的情况；修改 `.env` 或 `data/api.env` 后仍须重新创建容器。

5. 做只读检查，再由调用方串行发送一个通用测试；成功后恢复业务。源码版检查见第 3 节；拉取版使用：

```sh
sudo docker compose -f compose.image.yaml exec -T chatgpt-web2api python /app/scripts/check-nas-api.py --wait 120
```

注意：页面有输入框、`/api/auth/session` 返回 200 或健康接口可访问，都不能单独证明登录凭据有效。目前 `/v1/models` 获取真实目录失败时可能返回 3 个默认模型，因此诊断显示 `Models: 3` 或“只读检查通过”也不是聊天验收通过；实际模型数量随账号与网站变化，不要求固定为某个数值。刚启动且尚无聊天请求时，健康状态可能为 `starting`。检查浏览器连接、错误与暂停状态，并确认实际测试回复完整。

不要恢复旧文件名来尝试修复新登录，也不要把浏览器恢复探针当作登录操作；它只检查浏览器响应。仍出现 401 时，区分本服务 API Key 错误与 ChatGPT 网页凭据过期，查看脱敏日志后再处理。

## 3. 验收与使用

```sh
sudo docker compose -f compose.yaml -f compose.headed.yaml exec -T chatgpt-web2api python - < scripts/check-nas-api.py
```

该命令从 NAS 目录传入脚本，只检查健康与模型，不依赖镜像内是否包含 scripts。需主动发送一次测试时，将 `python - <` 改成 `python - --send <`，要求返回 OK。
API Base URL 为 `http://192.168.1.100:11111/v1`，模型 auto，先关闭流式。
见 [API 接入手册](API使用与项目接入手册.md)。健康正常、HTTP200 都不能代替正文验收。

## 4. 排查

| 现象 | 检查方向 |
|---|---|
| 6080 显示目录列表 | 旧镜像缺少默认首页；先用 /vnc.html，新镜像已补入口 |
| 检查脚本不存在 | 用上面的 stdin 传入方式，不必只为诊断脚本重新部署 |
| apt-key 错误 | 是否使用本 Fork 的 Dockerfile |
| Chrome 启动失败 | 容器日志、目录权限、profile 是否被另一实例占用 |
| 无法联网 | 代理监听地址、HTTP/SOCKS 端口类型、NAS 网络 |
| Cookie 无效 | 导出格式、有效期、ChatGPT 网页实际登录状态 |
| 网页有答案但接口失败 | 会话 ID 和采集故障；先看日志，避免连续重发 |
| 401 / token_expired | 区分本地 API Key 错误和网页登录过期；见[重新登录步骤](#login-recovery) |
| 429 | 按 Retry-After 等待，不要无限重试 |
| 503/504 | 锁等待、熔断、生成停滞和网络 |

```sh
sudo docker logs --tail 150 chatgpt-web2api
sudo sh scripts/diagnose-nas-auth.sh
```

日志可能含提示词或账号信息。只在本地查看，公开问题前脱敏。不要反复删除 profile 试错。

## 5. 日常维护

每次 Compose 操作均使用两个配置文件，避免意外切换为 headless。

```sh
sudo docker compose -f compose.yaml -f compose.headed.yaml ps
sudo docker compose -f compose.yaml -f compose.headed.yaml logs --tail 100
sudo docker compose -f compose.yaml -f compose.headed.yaml stop
sudo docker compose -f compose.yaml -f compose.headed.yaml up -d --no-build
```

升级前备份代码、镜像和私有数据；复制完整 profile 前先停止服务，避免数据库不一致。
备份含登录凭据，不能公开。更新后重新运行构建脚本，再做一次聊天验收。

## 6. 验证范围

新版 REST API 支持文本和有限大小的 Base64 图片，原文档上传仍不支持。图片功能需要重建镜像，接入与本地 Docker 验证范围见[图片说明](docs/IMAGE-INPUT.md)；NAS 图片、新建会话和续聊的最小实机验收已通过，范围见图片说明。流式、多客户端及长期稳定性未在此 NAS 完整验收。
网页读取兜底有回归测试，但尚未单独完成 NAS 实机验收。关闭客户端重试不代表服务内部不会重试。
独立安装验收见下一节；不同宿主机和网络仍需验证。


## 7. 独立安装验收记录（2026-09-20）

在 Windows Docker Desktop 的 Linux/amd64 环境，从公开仓库重新克隆，以空 profile 和独立测试密钥构建；未复制原 NAS 的登录数据。
换用清华 Debian/PyPI 镜像并分别配置构建与运行代理后，两层镜像及安装脚本运行成功。
首次人工登录等待和虚拟桌面残留锁问题已修复，实际重启后 noVNC 与 Chrome CDP 均可访问。
人工登录后，模型目录返回21项；仅发送一次 auto、stream=false 的最小聊天请求，API 正文返回 OK，随后健康检查通过。

此结果验证上述本机容器环境，不能替代其他 NAS 的实机兼容性测试。未验证流式、多客户端或长时间稳定性。


## 8. 协议读取与合成测试验收（2026-09-20）

回复完整性修复与 backend 模式此前通过 146 项相关离线回归；本机单次合成 JSON 测试逐字一致。本次图片、会话控制与启动检查共通过 153 项相关测试。在一台 amd64 NAS 上，使用合成图片和虚构测试编号验证了新建、图片识别、SSE 追问与按 ID 切换。详见[图片验证记录](docs/IMAGE-INPUT.md)。

这些最小合成测试不代表长文本、复杂识图、高并发及长期稳定性已经全面验证。业务结果的语义与格式仍由调用方校验。公开文档不包含下游项目实例。
