# API Reference

ChatGPT-Web2API exposes two interfaces: an OpenAI-compatible REST API and an MCP server.

## REST API

Base URL: `http://localhost:8080/v1`

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
data: {"id":"conv-abc123","object":"chat.completion.chunk","choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}

data: {"id":"conv-abc123","object":"chat.completion.chunk","choices":[{"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

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

All errors return standard OpenAI-compatible JSON. Two error shapes exist:

### Server errors (HTTP 500)

Driver/processing failures — Chrome disconnects, timeouts, navigation or login
problems. These are real failures, **not** retriable:

```json
HTTP/1.1 500
{
  "error": {
    "message": "<details>",
    "type": "server_error"
  }
}
```

### Rate limits (HTTP 429) — retriable

ChatGPT's "Too many requests" throttling. The server first tries to recover
**transparently**: it dismisses the pop-up and retries your request up to 3
times with backoff. Only if the limit *persists* does it surface this
standard OpenAI `429`, which the OpenAI SDK / LangChain / LlamaIndex auto-retry:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 60
{
  "error": {
    "message": "ChatGPT rate limit reached (Too many requests). Retry in 60s.",
    "type": "rate_limit_exceeded",
    "param": null,
    "code": "rate_limit_exceeded"
  }
}
```

`Retry-After` is parsed from the pop-up text when ChatGPT gives an exact
number; otherwise it defaults to a conservative 60s.

### Streaming caveat

A 429 can be returned before streaming begins (a pre-flight rate-limit check
runs before the SSE response is committed). A rate limit that appears
*mid-stream* — after the `200 OK` is sent — cannot change status, so it is
surfaced as an inline SSE chunk:

```
data: {"choices":[{"delta":{"content":"\n\n[Error: rate_limit_exceeded — retry in 60s]"},"finish_reason":"error"}]}
data: [DONE]
```

Prefer **non-streaming** requests if your agent needs reliable 429 detection.
