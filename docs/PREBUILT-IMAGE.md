# Prebuilt Docker image / 直接拉取镜像

中文 · [English](#english) · [README](../README.md)

镜像：`ghcr.io/zifly/chatgpt-web2api-nas:latest`。这是 GitHub Container Registry（GHCR），不需要 Docker Hub 账号。公开权限开启后可匿名拉取。镜像包含 API、Chrome、Xvfb、VNC/noVNC、图片输入和会话控制；仍需登录自己的 ChatGPT 账号。仅支持 `linux/amd64`（Intel/AMD 64 位 NAS），不提供 ARM 镜像。

## 新安装

在 NAS SSH 终端中建立一个空目录，下载两个公开配置文件，也可以使用已克隆的仓库：

```sh
mkdir -p chatgpt-web2api-nas
cd chatgpt-web2api-nas
curl -fL https://raw.githubusercontent.com/zifly/chatgpt-web2api-nas/master/compose.image.yaml -o compose.image.yaml
curl -fL https://raw.githubusercontent.com/zifly/chatgpt-web2api-nas/master/.env.example -o .env.example
mkdir -p data/chrome-profile data/cookies
cp .env.example .env
```

按[安装手册的私有配置说明](../NAS安装与故障排查手册.md)创建以下文件。不要用示例占位符当真实密码：

- `data/api.env`：一行 `W2A_API_KEYS=你自己生成的长随机密钥`。
- `data/vnc-password.txt`：你自己的 VNC 密码。传统 VNC 仅使用前八个字符。
- `.env`：设置 `W2A_BIND_ADDRESS` 为自己的 NAS 局域网 IP；默认只监听 NAS 的 `127.0.0.1`。按需设置 `W2A_REPLY_SOURCE=backend`、`W2A_PROXY_URL`。

然后直接拉取、启动，无需下载 Python 或编译依赖：

```sh
sudo docker compose -f compose.image.yaml pull
sudo docker compose -f compose.image.yaml up -d --no-build
sudo docker compose -f compose.image.yaml logs --tail 100
```

老版本 Compose 可将 `docker compose` 换成 `docker-compose`。**这个文件独立使用，不要与 `compose.yaml` 或 `compose.headed.yaml` 合并。**

访问 `http://NAS-HOST:6080/`，输入 `data/vnc-password.txt` 中的密码，在远程浏览器登录 ChatGPT。API 地址是 `http://NAS-HOST:11111/v1`。Chrome 同步账号与 ChatGPT 网站登录是两回事；Cookie JSON 是可选方式，不必先复制 Cookie 才能登录。

只读诊断（不会发送聊天消息）：

```sh
sudo docker compose -f compose.image.yaml exec -T chatgpt-web2api python /app/scripts/check-nas-api.py --wait 120
```

登录前 API 可能尚未就绪；诊断超时不代表镜像下载失败。图片及会话参数见[图片输入](IMAGE-INPUT.md)与[会话控制](CONVERSATION-API.md)。

## 网络设置

镜像依赖已在 GitHub 构建好，NAS 安装不需要清华源或 `W2A_BUILD_PROXY_URL`。

- 镜像下载慢或无法连接 GHCR：检查 **Docker 守护进程/Container Manager** 的网络或代理设置。
- Chrome 无法访问 ChatGPT：按需在 `.env` 配置 **`W2A_PROXY_URL`**，然后重新创建容器。它不控制 Docker 拉取镜像。
- 代理如果运行在 NAS 上，容器内的 `127.0.0.1` 并不指向 NAS。参阅安装手册的代理说明。

## 从源码部署迁移、更新和回滚

先等正在进行的后端任务完成。在原项目目录使用 `compose.image.yaml`，继续使用同一套 `.env`、`data/api.env`、VNC 密码和 Chrome 资料。先拉取新镜像，成功后再停止旧服务。

迁移前记录 `docker inspect chatgpt-web2api --format '{{.Image}}'` 的旧镜像 ID，给它加一个本地备份标签，并在停止容器后备份 `data/` 与 `.env`（备份含账号资料，只保存在自己控制的存储中）。添加发布工作流不会自动更新任何正在运行的 NAS。

源码版首次迁移时：

```sh
sudo docker compose -f compose.image.yaml pull
sudo docker compose -f compose.yaml -f compose.headed.yaml stop chatgpt-web2api
```

已使用拉取版时，改用：

```sh
sudo docker compose -f compose.image.yaml pull
sudo docker compose -f compose.image.yaml stop chatgpt-web2api
```

确认没有其他容器或 Chrome 使用同一个资料目录，再清理已停止 Chrome 留下的单实例符号链接并启动：

```sh
sudo docker compose -f compose.image.yaml run --rm --no-deps --entrypoint sh chatgpt-web2api -ec '
for name in SingletonLock SingletonSocket SingletonCookie; do
    lock="/data/chrome-profile/$name"
    if [ -L "$lock" ]; then unlink "$lock"; fi
done
'
sudo docker compose -f compose.image.yaml up -d --no-build
```

不要同时启动新旧容器共用 Chrome 资料。更新不删除 `data/`，但新 Chrome 可能升级资料格式；回滚旧 Chrome 时可能需要恢复停机备份的资料。

`latest` 随主分支的程序/构建改动更新；每次构建还提供 `sha-完整40位提交号`，版本标签构建提供对应的 `v...` 标签。发布版本标签不会移动 `latest`。需要固定版本时在 `.env` 设置 `W2A_IMAGE=ghcr.io/zifly/chatgpt-web2api-nas:sha-完整提交号`，也可使用 GHCR 的 `@sha256:...` 摘要严格固定镜像。回滚时切回已记录的旧镜像标签/摘要，按上述停机流程重新创建容器。

## 维护者：自动发布

[Publish NAS image](../.github/workflows/publish-image.yml) 在主分支的程序/构建文件更新、`v*` 标签推送或手动运行时构建。使用 GitHub 临时的 `GITHUB_TOKEN` 推送 GHCR，无需把账号密钥写入仓库。构建后的检查只检查程序、Chrome、桌面组件和打包文件，不登录 ChatGPT、不发送测试提示词。

首次成功构建后，到 GitHub 个人主页的 **Packages → chatgpt-web2api-nas → Package settings → Change visibility → Public**，再验证匿名拉取。**源码仓库公开，不代表新建的 GHCR 包自动公开。** 若 fork 的 Actions 尚未启用，需要先在仓库 Actions 页面启用。其他 fork 要自行修改工作流的仓库限制及镜像名。

参考：[GitHub 镜像发布](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)、[GHCR 权限与可见性](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)。

## English

Pull `ghcr.io/zifly/chatgpt-web2api-nas:latest` using the standalone `compose.image.yaml`. It includes Chrome, the browser desktop, REST API, image input and conversation controls. Only `linux/amd64` is supported. You still need your own ChatGPT login; no credentials or browser profiles are shipped.

1. Download `compose.image.yaml` and `.env.example` using the commands above, or use a clone. Create `data/chrome-profile` and `data/cookies`.
2. Copy `.env.example` to `.env` on a new installation only. Set your NAS LAN IP with `W2A_BIND_ADDRESS` (the default is loopback). Optionally set `W2A_REPLY_SOURCE=backend` and `W2A_PROXY_URL`.
3. Create `data/api.env` with `W2A_API_KEYS=` followed by your own long random service key. Create `data/vnc-password.txt` with your private VNC password; traditional VNC uses only the first eight characters. See [private configuration](../NAS-INSTALLATION.md#1-private-configuration).
4. Run `sudo docker compose -f compose.image.yaml pull`, then `sudo docker compose -f compose.image.yaml up -d --no-build`. Use `docker-compose` on older systems. Do not merge this file with the source-build Compose files.
5. Open `http://NAS-HOST:6080/` and log in to ChatGPT. The API base URL is `http://NAS-HOST:11111/v1`. Cookies are optional. Run the read-only diagnostic shown above after login.

Prebuilt images need no local package mirrors or build proxy. Configure the Docker daemon's proxy for GHCR pulls if needed; `W2A_PROXY_URL` only configures the browser/application at runtime.

For migration, keep the same project directory and private configuration. Wait for active jobs to finish, record/tag the old image, pull the new image, then stop the old service and back up private data while stopped. Use the migration/update and stopped-profile cleanup commands above before starting the new container. Never let two Chrome instances share the profile. New Chrome versions can upgrade profiles, so rollback may also require restoring the stopped backup.

`latest` tracks application/build changes on `master`; builds also publish `sha-<full-40-character-commit>` and version-tag builds publish their `v...` tag without moving `latest`. Set `W2A_IMAGE` in `.env` to a saved tag or immutable digest to pin or roll back. Updates preserve mounted data and require container recreation.

Maintainers: the workflow builds from checked-out source and publishes using `GITHUB_TOKEN`. Checks run without an account or generated prompts. Enable Actions if necessary, and after the first publication change the package visibility to **Public** in GitHub Packages before testing anonymous pulls. Public repository visibility alone is insufficient. Fork maintainers must update the repository guard and image name in the workflow.
