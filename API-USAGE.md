# API usage and project integration

[中文](API使用与项目接入手册.md) · [README](README.md) · [NAS installation](NAS-INSTALLATION.md)

Updated: 2026-09-20. Applies to this NAS fork. `192.168.1.100` and `NAS-HOST` are examples; replace them with your own addresses.

## 1. Connection settings

| Setting | Value |
|---|---|
| Provider | OpenAI Compatible / Custom OpenAI |
| Base URL | `http://192.168.1.100:11111/v1` |
| Full chat URL | `http://192.168.1.100:11111/v1/chat/completions` |
| Model | `auto` |
| API key | A key from `W2A_API_KEYS` in the NAS project's `data/api.env` |
| Header | `Authorization: Bearer <API key>` |
| Initial settings | `stream: false`, concurrency 1, automatic client retries disabled |
| Client timeout | 180 seconds; this does not change the server timeout |

For a base URL, stop at `/v1`. For a full endpoint, include `/chat/completions`. Avoid duplicating `/v1`.

On Windows the shared key file is `\\NAS-HOST\docker\chatgpt-web2api\data\api.env`. Copy only the value after `=`, not the variable name. If keys are comma-separated, choose one. This is the service's access key, not an official OpenAI API key or your Google password.

The client must reach the NAS LAN. Another Docker container should use the NAS IP and published port 11111; its own `localhost` does not refer to this NAS service. Cloud-hosted clients normally need additional private networking to reach a LAN address.

## 2. Capabilities and limits

| Feature | Current behavior |
|---|---|
| Text requests | Minimal OK checks and fixed structured business samples passed |
| Multi-turn text | History messages and custom `conversation_id` are implemented; see section 5 |
| SSE | Content is buffered until final verification, not delivered token by token; NAS SSE acceptance is outstanding |
| Images / `image_url` | Not uploaded or supported by this REST interface |
| Original PDF, Word, Excel or other files | Not supported; no `/v1/files` endpoint |
| Document text | Extract text in the calling project; OCR is also the caller's responsibility |
| Tool/function calling | Not implemented by the REST chat handler |
| Responses API | No `/v1/responses`; select Chat Completions |
| Embeddings / audio | Not provided |
| `temperature` / `max_tokens` | Do not control webpage generation |
| Enforced JSON | No enforced structured-output contract; request JSON in text and validate it |
| Token usage | Zero placeholders, not actual usage or billing |

This service implements part of the OpenAI request/response format over the ChatGPT website. Model listings and health checks do not prove that chat generation works. `auto` uses the webpage default. If explicit model selection fails, the implementation may continue using the active webpage model; the response's `model` field is not proof that selection succeeded.

## 3. Minimal request and acceptance

POST this JSON to the full chat URL with authorization and `Content-Type: application/json`:

```json
{
  "model": "auto",
  "messages": [
    {"role": "system", "content": "This is an independent test. Answer only this request."},
    {"role": "user", "content": "Reply with exactly: OK"}
  ],
  "stream": false
}
```

Example response shape:

```json
{
  "object": "chat.completion",
  "model": "auto",
  "conversation_id": "web-conversation-id",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "OK"},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

Read `choices[0].message.content`. For this initial test, require HTTP 200 and trimmed content equal to `OK`. Empty text is failure. The custom `conversation_id` may be empty when a fresh-page fallback is allowed; the top-level `chatcmpl-...` identifier is not a webpage conversation ID. Backend-only success requires a resolved conversation ID.

A short OK check tests basic connectivity, not long structured-output integrity. Validate representative business samples before adopting results.

## 4. Client examples

### Windows PowerShell without an SDK

Run on the client computer and enter the service key at the prompt. Send once and wait; after a timeout, inspect the webpage and logs before sending again.

```powershell
$nasKeySecure = Read-Host 'Paste the NAS API key' -AsSecureString
$nasApiKey = [System.Net.NetworkCredential]::new('', $nasKeySecure).Password
$nasHeaders = @{ Authorization = "Bearer $nasApiKey" }
$nasBody = @{
    model = 'auto'
    stream = $false
    messages = @(
        @{ role = 'system'; content = 'This is an independent test. Answer only this request.' }
        @{ role = 'user'; content = 'Reply with exactly: OK' }
    )
} | ConvertTo-Json -Depth 8
$nasReply = Invoke-RestMethod `
    -Uri 'http://192.168.1.100:11111/v1/chat/completions' `
    -Method Post -Headers $nasHeaders -ContentType 'application/json; charset=utf-8' `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($nasBody)) -TimeoutSec 180
$nasReply.choices[0].message.content
```

### Python standard library

First set `NAS_CHATGPT_API_KEY` in the client process environment. This example does not read the NAS shared file, does not retry automatically, and bypasses system HTTP proxies for the LAN request.

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
        {"role": "system", "content": "This is an independent task. Process only this input."},
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
    # Inspect errors locally; do not put raw errors or prompts in public logs.
    raise RuntimeError(
        f"NAS API HTTP {exc.code}: " + exc.read().decode("utf-8", errors="replace")
    ) from exc

choices = result.get("choices") or []
text = choices[0].get("message", {}).get("content") if choices else None
if not isinstance(text, str) or not text.strip():
    raise RuntimeError("Empty response; inspect the NAS browser and logs")
print(text)
if text.strip() != "OK":
    raise RuntimeError("The reply did not pass this OK check")
```

### Existing OpenAI Python SDK projects

Use the SDK version already managed by your project. This configuration example was not separately executed during acceptance; validate it with your dependencies. It uses the same `NAS_CHATGPT_API_KEY` environment variable.

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
        {"role": "system", "content": "This is an independent task. Process only this input."},
        {"role": "user", "content": "Reply with exactly: OK"},
    ],
)
text = reply.choices[0].message.content
if not text or not text.strip():
    raise RuntimeError("Empty NAS API response")
print(text)
```

If the client uses a global proxy, add the NAS address to that process's `NO_PROXY`, preserving existing entries.

## 5. Independent tasks and continued conversations

### A. Independent tasks

Include a `system` message on every request and omit `conversation_id`. The current implementation then opens a new webpage conversation instead of automatically reusing the previous chat.

System content is flattened into `[System Instructions]` text in the webpage prompt. It does not have the independent channel semantics or authority of the official API's system message.

### B. Client-managed history

Include a system message and the user/assistant history you need, without a conversation ID. The service opens a new chat and flattens history into text. Only the last 20 user/assistant messages are retained by the current implementation. Summarize or trim long input in the client.

### C. Continue a webpage conversation

Start with A, save the nonempty `conversation_id`, and then send only the new user content:

```json
{
  "model": "auto",
  "conversation_id": "actual-id-from-the-previous-response",
  "messages": [{"role": "user", "content": "Explain the previous answer further."}],
  "stream": false
}
```

An SDK can pass this extension through `extra_body={"conversation_id": "..."}`. Access to custom response fields depends on the wrapper; plain HTTP JSON is an alternative.

Do not resend the entire history while also continuing the original conversation, or context will be duplicated. Keep separate IDs for separate business conversations. When both system and conversation ID are absent, the service may reuse its last chat if its conditions match. Omitting the ID alone does not guarantee a fresh conversation; this shared state is not multi-user isolation.

Start with serial requests. Do not manually switch chats or send messages in noVNC while a request is running.

## 6. Working with documents

Extract TXT/Markdown text or use your project's PDF/DOCX/XLSX parser, then submit ordinary text:

```json
{
  "model": "auto",
  "stream": false,
  "messages": [
    {"role": "system", "content": "Summarize the supplied document text. State uncertainties."},
    {"role": "user", "content": "Summarize this document:\n\nText extracted by the calling project goes here."}
  ]
}
```

This does not upload the original file. Images, layout and table structure may be lost. A local path, NAS path or base64 string does not cause the service to open an attachment. Split long documents into manageable parts; the HTTP request-size limit is not the model context limit.

## 7. Checks and error handling

- Health: `http://192.168.1.100:11111/health`
- Models: `http://192.168.1.100:11111/v1/models`, with Bearer authentication
- Desktop: `http://192.168.1.100:6080/` or `/vnc.html`

Inspect `chrome_running`, `driver_connected`, `last_error` and `open_breakers`. Immediately after startup, `starting` may mean no chat has completed yet. The model list can be a fallback list, so it does not prove message sending works.

| Symptom | First checks |
|---|---|
| Connection refused | NAS IP, port 11111, container, client network and proxy |
| 404 | Wrong Responses endpoint or duplicated `/v1` |
| 400 | JSON shape and a user message in `messages` |
| 401: Invalid API key / auth_error | Local service key |
| 401: expired login / invalid_api_key details | Website authentication may have expired; inspect the desktop |
| 429 | Respect `Retry-After`; internal backoff may already have occurred |
| 503 | Error codes such as `lock_timeout` or `circuit_open`; inspect queueing and browser state |
| 504 / generation_stuck | Generation stall, webpage state and proxy connectivity |
| 500 or timeout | The message may already have been sent; inspect before retrying |
| HTTP 200 with empty content | Treat as failure; do not forward as a valid business result |
| Page answered but API failed | Reply collection/correlation; not by itself a reason to log into Google again |

Run in a NAS SSH terminal:

```sh
sudo docker logs --tail 150 chatgpt-web2api
```

Read-only check from the NAS project directory:

```sh
sudo docker compose -f compose.yaml -f compose.headed.yaml exec -T chatgpt-web2api python - < scripts/check-nas-api.py
```

This passes the script from the NAS into the container, avoiding missing-script errors in older images. To send one real test message, replace `python - <` with `python - --send <`.

With SSE, HTTP 200 can already have been sent before an error occurs. `finish_reason: "error"` or an `[Error: ...]` event means failure even if `[DONE]` follows. Start with non-streaming calls.

## 8. Handoff checklist for another project

Copy this into a developer task and supply the key through private server configuration:

```text
Integrate this project with the existing NAS ChatGPT-Web2API text service.
Base URL: http://192.168.1.100:11111/v1
Endpoint: POST /chat/completions relative to the base URL
Model: auto
Authorization: Bearer key from NAS_CHATGPT_API_KEY

1. Use Chat Completions, not Responses API.
2. Start with stream=false, concurrency 1, timeout 180 seconds, and no automatic client retries.
3. Include a system message and omit conversation_id for every independent task.
4. Send string text only; do not assume image_url, attachments, tools or response_format are supported.
5. Read choices[0].message.content; empty output is failure.
6. Send one 'Reply with exactly: OK' check, then validate representative business samples.
7. On timeout, inspect the NAS browser/logs before retrying. Distinguish key errors from expired website login.
8. Keep keys in private server configuration, never source code, frontend code or Git.
9. Extract document text in this project; original-file/image uploads are unsupported.
10. Preserve existing NAS Docker, profile, cookie and proxy settings.
11. Validate JSON/schema, case IDs and candidate IDs; never guess repairs.
Report changes, actual tests and remaining incompatibilities.
```

## 9. Reply integrity and server modes

The old algorithm sliced new webpage text by the previous snapshot length. Prefix rewrites could lose or duplicate characters. The fix returns the entire verified final answer rather than guessing JSON repairs or business IDs.

| Server setting | Behavior |
|---|---|
| `W2A_REPLY_SOURCE=reconciled` (default) | Completion detection then full current-turn reconciliation; a strictly guarded stable fresh-WEB page fallback is allowed |
| `W2A_REPLY_SOURCE=backend` | Browser login/send, followed by conversation-protocol completion and full text; no assistant DOM scraping or page fallback |

This is server configuration, not a per-request parameter. NAS business tests used `backend`. It requires a persistent conversation ID and trustworthy turn match; uncertain, empty or timed-out replies fail. In reconciled mode, page fallback can read an isolated code block's body but rejects mixed prose/code or multiple code blocks.

Both modes buffer SSE content until final verification. Clients and reverse proxies must tolerate the generation wait. This does not force the model to produce valid JSON or correct business decisions.

## 10. Validation scope

API fields, conversation branches and errors were checked against the source. The combined reply fix/backend mode passed 146 related offline tests and one exact synthetic JSON request locally. On the NAS, three fixed business samples ran in two rounds: six requests and ten work-group results passed JSON/schema/case-ID/candidate-pool checks. The second round used the real review page; results stayed pending without changing formal library associations.

These business requests were not independently compared against same-turn protocol originals. The main-library UI after administrator login was not tested. Long text, multi-turn, high concurrency, sustained stability and the NAS SSE path remain to be accepted. Business meaning still requires review. Test client examples in your own network and dependency environment; the SDK example was not separately executed.
