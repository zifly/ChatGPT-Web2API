# 已有镜像部署 / Image deployment

[README](../README.md) · [安装与私有配置](../NAS安装与故障排查手册.md)

此处提供独立的 `compose.image.yaml` 模板。需要先取得由可信来源构建、包含所需功能的 `linux/amd64` 桌面镜像。源码提交与镜像发布是两个步骤，不要假定旧镜像已经支持 REST 并发。本次 Git 提交不发布远程镜像。

按安装手册准备 `.env`、`data/api.env`、`data/vnc-password.txt`、浏览器资料目录及可选 Cookie 目录。将真实镜像地址写入私有 `.env`，下面仅为占位示例：

```dotenv
W2A_IMAGE=registry.example.com/your-project/chatgpt-web2api:your-version
W2A_BIND_ADDRESS=192.168.1.100
W2A_REPLY_SOURCE=backend
# W2A_PROXY_URL=http://host.docker.internal:3128
```

```sh
sudo docker compose -f compose.image.yaml pull
sudo docker compose -f compose.image.yaml up -d --no-build
sudo docker compose -f compose.image.yaml exec -T chatgpt-web2api python /app/scripts/check-nas-api.py --wait 120
```

这个文件独立使用，不要与源码构建的 Compose 文件合并。访问自己 NAS 的 `6080` 端口完成 ChatGPT 登录，API 为 `11111/v1`。密钥与登录资料不会随镜像提供。

更新前等待在途请求结束，并记录当前镜像版本或摘要以便回退。明确选择目标版本后再拉取和重建容器，已有部署复用私有配置。启动后应检查 `/health.rest_pool` 并按[接入清单](REST-CONCURRENCY.md)验收。

## English

Set `W2A_IMAGE` to a trusted desktop image built from the required source version. The registry address above is a placeholder, not a published image. This Git commit does not publish an image.

Use `compose.image.yaml` on its own, create the private files described in the installation guide, then pull and start the image. Published ports default to loopback unless a bind address is configured. The browser still requires your own ChatGPT login. Runtime proxy settings do not configure Docker registry pulls.

Before an update, drain active requests and retain the previous image reference. Confirm pool availability and run the integration checklist after starting the selected version; source changes alone do not update an existing container.
