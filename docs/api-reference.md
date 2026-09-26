# API Reference

ChatGPT-Web2API exposes two interfaces: an OpenAI-compatible REST API and an MCP server.

## REST API

For this NAS deployment, the client Base URL is `http://<NAS-LAN-IP>:11111/v1`.
Replace `<NAS-LAN-IP>` with your NAS address and set `W2A_BIND_ADDRESS` in `.env` to that address to allow LAN access. The default host binding is loopback-only; on the NAS host itself, use `http://127.0.0.1:11111/v1`.

Inside the API container, the service listens on port `8080` (`http://127.0.0.1:8080/v1`). Docker maps host port `11111` to container port `8080`. A different container should use the NAS address and published port, or the service name and port `8080` on a shared Docker network; its own `localhost` is not the API container.

Send `Authorization: Bearer <YOUR_API_KEY>` using a key configured in the private `data/api.env` file. See the [NAS setup guide](../NAS安装与故障排查手册.md) and [client integration guide](../API使用与项目接入手册.md) for the tested non-streaming text workflow.

For this NAS fork, see the [calling guide](../API-USAGE.md) and
[concurrent conversation migration](REST-CONCURRENCY.md). Pooled REST can run
distinct conversations concurrently while serializing each ID within one REST
process. Check `/health.rest_pool` for the deployed capacity. Missing IDs always
start fresh in pool mode; singleton mode retains legacy implicit selection.

### Chat Completions

```
POST /v1/chat/completions
```

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | yes | Model slug (see `/v1/models`). Use `"auto"` for default. |
| `messages` | array | yes | Array of `{"role": "user"/"assistant", "content": "..."}` |
| `stream` | boolean | no | Enable SSE streaming (default: `false`) |
| `new_conversation` | boolean | no | Force a fresh chat; cannot be true with a nonempty conversation ID |
| `conversation_id` | string | no | Resume the saved webpage ID; send only new turn content |
| `progress_id` | UUID string | no | Fresh per-attempt ID for concurrent progress polling with the same API key; not a conversation ID or durable idempotency key |
| `temperature` | float | no | Ignored — ChatGPT controls this |
| `max_tokens` | int | no | Ignored — ChatGPT controls this |

**Response (non-streaming):**

```json
{
  "id": "chatcmpl-response-id",
  "conversation_id": "actual-web-conversation-id",
  "object": "chat.completion",
  "model": "auto",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "Hello! How can I help?"},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

**Response (streaming):**

```
data: {"id":"chatcmpl-response-id","object":"chat.completion.chunk","choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-response-id","object":"chat.completion.chunk","conversation_id":"actual-web-conversation-id","choices":[{"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### Request progress

`GET /v1/requests/{progress_id}` returns the stage and elapsed timings of an
opted-in chat, including while a non-streaming POST is still waiting. Use the
same API key as the POST and poll about once per second. Check
`/health.request_progress.supported` for availability. Status is `running`,
`succeeded`, `failed` or `cancelled`; the original POST supplies the actual
answer, conversation ID and error. Unknown/expired/other-key records return 404.

Storage is limited to 256 records in one REST process; completed records expire
after 300 seconds and disappear on restart. Duplicate registered IDs return 409
before browser work. Progress contains no chat content and never sends or
replays a message. Final `request_diagnostics` adds `phase`,
`phase_elapsed_seconds` and cumulative `phase_timings`, even without polling.
See [schema, stage labels and caller requirements](REQUEST-PROGRESS.md).

### Usage statistics

Open `/stats` for the built-in dashboard, or query `GET /v1/stats?period=today`
with the same service API key used for chat. Supported periods are `today`,
`7d`, and `30d`, using Shanghai dates. Statistics are key-scoped and cover
authenticated terminal REST chat attempts, including failures and cancellation.
Progress polls are excluded. SQLite history is retained for 90 days by default;
the Compose deployment persists it in `data/usage`.

The response contains `summary`, `daily`, `phase_averages`, `recent`, `live`,
`capacity`, `storage`, and recording/report timestamps. Byte counts describe
application bodies, not browser traffic or token usage. See the
[complete metric definitions and retention contract](USAGE-DASHBOARD.md).

### Models

```
GET /v1/models
```

Returns available ChatGPT models:

```json
{
  "object": "list",
  "data": [
    {"id": "auto", "object": "model", "owned_by": "chatgpt"},
    {"id": "gpt-5-5", "object": "model", "owned_by": "chatgpt"}
  ]
}
```

## MCP Tools

### Chat & Completion

| Tool | Input | Output |
|------|-------|--------|
| `chat_completion` | `message`, `system_prompt?`, `model?`, `conversation_id?`, `project_id?` | Response text + metadata |
| `chat_with_gpt` | `gpt_id`, `message` | Response text |

### Read Operations

| Tool | Input | Output |
|------|-------|--------|
| `list_models` | — | Model catalog |
| `list_projects` | — | Project list with IDs |
| `list_conversations` | `limit?`, `offset?` | Conversation list |
| `get_conversation` | `conversation_id` | Full message tree |
| `list_memories` | — | Memory list with IDs |
| `list_gpts` | — | Custom GPT catalog |
| `list_project_files` | `project_id` | File listing |

### Write Operations

| Tool | Input | Output |
|------|-------|--------|
| `create_project` | `name` | Project ID |
| `update_project_instructions` | `project_id`, `instructions` | Confirmation |
| `create_memory` | `content` | Confirmation |
| `archive_conversation` | `conversation_id`, `archive` | Confirmation |
| `delete_conversation` | `conversation_id` | Confirmation |
| `delete_memory` | `memory_id` | Confirmation |

## Model Mapping

The API maps common OpenAI model names to ChatGPT web equivalents:

| Requested | Maps to |
|-----------|---------|
| `auto` | ChatGPT default (reasoning model) |
| `gpt-4o` | `auto` |
| `gpt-4` | `gpt-5` |
| `gpt-3.5-turbo` | `gpt-5-mini` |

Use `list_models` for the current live catalog.

## Error Handling

REST chat failures return an OpenAI-shaped top-level `error`, including a
`new_conversation_retry` policy. Ordinary HTTP/SDK replay must stay disabled
(`max_retries=0`). A gateway may instead abandon the old attempt and retry once
in a new conversation under the [replacement contract](NEW-CONVERSATION-RETRY.md).
The gateway owns the task-wide retry budget and must rebuild context/images,
discard late old-attempt events and save the successful replacement's new ID.

### Error classification

| Response | Replacement policy |
|---|---|
| HTTP 5xx | Allowed once after the advertised delay, even if `send_state` is `unknown` or `confirmed`; explicit `client_disconnected` is excluded |
| HTTP 429 | Allowed once after the advertised account cooldown; new conversations do not bypass it |
| Other HTTP 4xx, including 400/401 | Not allowed; correct input, credentials or login first |
| Network timeout without an error body | The gateway may apply the same explicit, bounded policy; user cancellation must not trigger it |

The REST driver does not replay a failed send inside the same request, including
rate-limit failures. Non-REST/MCP retry behavior is outside this contract.

### Server errors (HTTP 5xx)

For example, a request deadline can return HTTP 504 with the following fields
(additional diagnostics omitted):

```json
{
  "error": {
    "message": "Request deadline exceeded",
    "type": "server_error",
    "code": "request_timeout",
    "send_state": "unknown",
    "automatic_retry_allowed": false,
    "new_conversation_retry": {
      "allowed": true,
      "mode": "new_conversation",
      "max_retries": 1,
      "retry_after_seconds": 2
    }
  }
}
```

`automatic_retry_allowed=false` forbids ordinary replay; it does not disable
the separate replacement policy. `max_retries` is a policy hint, not a persisted
counter: the gateway must stop after its one replacement, even if another error
again advertises `allowed=true`. Disallowed policies return `max_retries=0`.

### Rate limits (HTTP 429)

HTTP 429 carries `Retry-After` and a structured error. For a 60-second cooldown,
the response includes these fields:

```json
{
  "error": {
    "message": "ChatGPT rate limit reached (Too many requests). Retry in 60s.",
    "type": "rate_limit_exceeded",
    "param": null,
    "code": "rate_limit_exceeded",
    "automatic_retry_allowed": false,
    "new_conversation_retry": {
      "allowed": true,
      "mode": "new_conversation",
      "max_retries": 1,
      "retry_after_seconds": 60
    }
  }
}
```

Wait at least the advertised cooldown. It applies across the shared account;
opening a new chat or probing browser recovery does not clear it.

### Streaming caveat

Before SSE begins, failures can return an ordinary HTTP error. After HTTP 200
headers are committed, failures use a top-level `error` event and
`finish_reason=error`. For example (additional diagnostics omitted):

```
data: {"error":{"code":"rate_limit_exceeded","automatic_retry_allowed":false,"new_conversation_retry":{"allowed":true,"mode":"new_conversation","max_retries":1,"retry_after_seconds":60}},"choices":[{"index":0,"delta":{},"finish_reason":"error"}]}

data: [DONE]
```

Read cooldown from the policy in the event; HTTP 200 and `[DONE]` alone do not
mean success. Only accept a complete successful stream with `stop` and `[DONE]`,
and keep the final `conversation_id` separate from the response `id`. During a
replacement, discard all old-attempt output and ID updates. See the
[gateway and frontend acceptance checklist](NEW-CONVERSATION-RETRY.md#4-验收).
