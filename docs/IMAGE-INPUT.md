# Image input / 图片识别

[README](../README.md) · [中文 API 手册](../API使用与项目接入手册.md) · [English API guide](../API-USAGE.md)

## 中文

`POST /v1/chat/completions` 支持在最后一条 user 消息中提交文字和图片。程序将图片交给 ChatGPT 网页的上传控件，等待服务器预览图加载且发送按钮可用，然后发送问题。返回值仍是文字；没有图片生成、编辑或下载接口。MCP 暂未增加图片参数。

这需要包含图片功能的新镜像；只重启旧镜像不会新增功能。网页账号和当前模型必须支持图片上传，仍受网页自身额度限制。带图片且未指定 `conversation_id` 的请求会开启新会话；后续追问可传响应中的 `conversation_id`，无需重复上传。

### 输入约束

- 使用 `image_url.url`，内容为 `data:image/png;base64,...`、`data:image/jpeg;base64,...` 或 `data:image/webp;base64,...`。
- 单张最多 4 MiB，单次最多 4 张，图片解码后的文件字节总量最多 6 MiB，每张最多 2000 万像素。HTTP JSON 请求总大小仍为 10 MiB。
- 暂不支持公网图片 URL、文件路径、SVG、GIF、动画图片或 PDF 等文档附件。调用方读取自己的图片文件并编码；服务不会按路径读取调用方磁盘。
- 图片只能放在最后一条 user 消息里。历史消息中的图片会明确报错，不会默默丢弃；图片多轮问答使用 `conversation_id`。
- `detail` 只能省略或设为 `auto`；具体图像处理由网页决定。
- 纯图片请求会自动附加英文提示“Describe the attached image(s).”；建议始终明确写出问题。

先使用非流式、并发 1、客户端超时 180 秒并关闭自动重试。服务最多等待上传确认 90 秒，再用剩余请求预算等待回答。上传失败不会发送问题，非流式图片请求不会通过限流重试器自动重发。底层其他机制不构成端到端“最多发送一次”的保证。

服务会创建随机文件名的临时图片，上传等待结束后清理本地临时文件。失败时尝试移除网页中未发送的附件；如果清理未确认，后续发送会再次检查。上传到 ChatGPT 的文件和会话按网站本身的行为保留，本地清理不代表删除网站上的文件。

### Python 调用示例

先设置 `NAS_CHATGPT_BASE_URL`（例如 `http://192.168.1.100:11111/v1`）和 `NAS_CHATGPT_API_KEY`。以下脚本只发送一次请求，图片由运行脚本的电脑读取，不要求复制到 NAS。

```python
import base64
import json
import os
from pathlib import Path
import urllib.request

picture = Path("photo.png")
image_url = "data:image/png;base64," + base64.b64encode(picture.read_bytes()).decode("ascii")
payload = {
    "model": "auto",
    "new_conversation": True,
    "stream": False,
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": "请描述这张图片，并读出可见文字。"},
            {"type": "image_url", "image_url": {"url": image_url}},
        ],
    }],
}
request = urllib.request.Request(
    os.environ["NAS_CHATGPT_BASE_URL"].rstrip("/") + "/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": "Bearer " + os.environ["NAS_CHATGPT_API_KEY"],
        "Content-Type": "application/json",
    },
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(request, timeout=180) as response:
    result = json.load(response)
print(result["choices"][0]["message"]["content"])
# To ask a follow-up, retain result["conversation_id"] and send it as a
# top-level field with the next text-only user message.
```

JPEG、WebP 文件需对应修改 MIME；Base64 要嵌在 `image_url.url`，直接粘进普通文字并不等于上传图片。OpenAI 兼容 SDK 可使用同样的 `messages` 结构。模型识别可能出错，尤其是小字、模糊图片和复杂图表，业务端仍需校验。

文字和图片都可用 `new_conversation: true` 明确新建聊天；续聊时去掉这个标记并传 `conversation_id`。两者冲突会返回 HTTP 400。

## English

The REST Chat Completions endpoint accepts text plus Base64 image data URLs in the **last user message**. It uploads through the authenticated webpage's image file input, waits for decoded server-hosted previews and an enabled send button, and returns the verified textual answer. Image generation, editing, downloads, document uploads and MCP image arguments are outside this feature.

Build the updated image; restarting an older image is insufficient. The signed-in account and active model must support image uploads and remain subject to website limits. Image requests without an explicit `conversation_id` start a new conversation. For follow-up questions, retain the returned `conversation_id` and send a new text message without re-uploading the image or including image history.

Supported MIME types: `image/png`, `image/jpeg`, `image/webp`. Maximum 4 images, 4 MiB per file, 6 MiB combined decoded file bytes and 20 megapixels per image. The JSON request limit is 10 MiB. Remote URLs, local paths, animation, SVG, GIF and document attachments are rejected. `detail` must be omitted or `auto`; processing is controlled by the webpage. Image-only input gets the default prompt `Describe the attached image(s).` Explicit questions are recommended.

Text and image requests can explicitly start a fresh chat with `new_conversation: true`. To continue, omit that flag and provide `conversation_id`. Conflicting controls return HTTP 400.

Use the Python example above with your own base URL, service key and local image. Match the MIME to the file. Start with `stream: false`, concurrency 1, a 180-second client timeout and no automatic client retries. Upload confirmation is bounded to 90 seconds; the remaining server request budget is used for the answer. Both streaming and non-streaming return text after final verification; SSE is buffered, not token-by-token.

Malformed images fail before browser mutation. Unconfirmed uploads prevent sending the prompt. Non-streaming image requests bypass the automatic rate-limit resend wrapper, but this is not an end-to-end exactly-once guarantee for every underlying mechanism. Temporary local files are removed, and unsent composer attachments are cleaned up where possible. Files already uploaded to ChatGPT follow the website's retention behavior; local cleanup does not delete remote files.

## Validation / 验证范围

2026-09-20, isolated Linux/amd64 Docker Desktop browser, backend reply mode:

- One-image non-streaming request: identified a red square and blue circle from a synthetic image (12.16 seconds).
- Two-image SSE request: correctly separated the first image's objects and the second image's green triangle; `finish_reason=stop` and `[DONE]` received (15.37 seconds).
- Text follow-up using `conversation_id`: correctly recalled the second image's color without re-uploading (8.04 seconds).

These are synthetic functional checks, not broad OCR/vision accuracy guarantees. NAS acceptance is recorded below. No private screenshots, cookies or account data are included in these fixtures or this document.

122 related offline tests passed, including malformed images, byte/pixel/count limits, history rejection, temporary-file cleanup on cancellation, upload-before-send ordering, failure preventing sends and image rate-limit requests not being replayed. The headed Docker image built successfully and the installed image modules imported without a source-directory override.


### NAS acceptance / NAS 实机验收（2026-09-20）

After rebuilding and deploying on one amd64 NAS, four serial real requests passed:

| Request | Result | Seconds |
|---|---|---|
| Text with new_conversation=true | Returned OK and conversation A | 17.45 |
| Two images with new_conversation=true | Correct colors/shapes and order; conversation B differs from A | 22.20 |
| SSE text follow-up with conversation_id=B | Recalled green; same ID, stop and DONE | 19.16 |
| Switch back using conversation_id=A | Recalled the synthetic test code; same ID A | 17.23 |

部署后的 NAS 已通过纯文字、双图识别、图片会话 SSE 追问及按 ID 切回原文字会话测试。全部 HTTP 200，两个新建请求的会话 ID 不同，续聊和切回均保持目标 ID。未重新上传图片即可追问。无自动重试；这不代表复杂 OCR、并发或长期稳定性已经全面验收。

The deployment's first immediate diagnostic ran before API startup completed and reported URLError. The API subsequently became reachable without another rebuild. The diagnostic now supports --wait 120, retrying only read-only startup checks; wrong credentials and open breakers fail promptly. Five additional regression tests cover this behavior. This script-only fix does not require a service restart.
