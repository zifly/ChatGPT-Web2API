# NAS 安装与故障排查手册（公开版）

本 Fork 基于 https://github.com/Octo-Lex/ChatGPT-Web2API ，保留原 MIT 许可证。
仅验证过一台 amd64 NAS 的文本非流式调用，不是所有 NAS 的兼容承诺。
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

创建 `data/api.env`，填入自己生成的长随机密钥：

```text
W2A_API_KEYS=REPLACE_WITH_YOUR_OWN_RANDOM_API_KEY
```

创建 `data/vnc-password.txt`，写入自己的 VNC 密码。传统 VNC 只使用前 8 个字符，桌面仅限可信局域网。
整个 data 目录都是私有运行数据，不提交到 Git。

## 2. 构建启动

```sh
sudo sh scripts/enable-nas-desktop.sh
```

脚本先构建基础镜像再构建桌面镜像。构建失败时查看终端及本机 desktop-build.log。
打开 `http://192.168.1.100:6080/vnc.html`，输入 VNC 密码，在浏览器里完成 ChatGPT 登录。
Chrome 同步登录和 ChatGPT 网站登录不同；网站可用时不必因同步提示而退出账号。

可选：将自己的 Cookie-Editor JSON 放到 `data/cookies/cookies.json`。启动时存在就会导入；旧 Cookie 可能覆盖新状态。
不要分享 Cookie 或 chrome-profile，也不要让多个 Chrome 共用同一个 profile。

## 3. 验收与使用

```sh
sudo docker compose -f compose.yaml -f compose.headed.yaml exec -T chatgpt-web2api python scripts/check-nas-api.py
```

该命令只检查健康与模型。在末尾加 `--send` 才发送一次真实聊天，要求返回 OK。
API Base URL 为 `http://192.168.1.100:11111/v1`，模型 auto，先关闭流式。
见 [API 接入手册](API使用与项目接入手册.md)。健康正常、HTTP200 都不能代替正文验收。

## 4. 排查

| 现象 | 检查方向 |
|---|---|
| apt-key 错误 | 是否使用本 Fork 的 Dockerfile |
| Chrome 启动失败 | 容器日志、目录权限、profile 是否被另一实例占用 |
| 无法联网 | 代理监听地址、HTTP/SOCKS 端口类型、NAS 网络 |
| Cookie 无效 | 导出格式、有效期、ChatGPT 网页实际登录状态 |
| 网页有答案但接口失败 | 会话 ID 和采集故障；先看日志，避免连续重发 |
| 401 | 区分本地 API Key 错误和网页登录过期 |
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

当前 REST API 为文本接口，无图片和原文件上传。流式、多客户端及长期稳定性未在此 NAS 验收。
网页读取兜底有回归测试，但尚未单独完成 NAS 实机验收。关闭客户端重试不代表服务内部不会重试。
公开版调整了镜像名称与配置变量，需要在新部署环境验证构建和登录；不保证开箱即用。
