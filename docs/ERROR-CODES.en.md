# REST error codes and client handling

[中文](ERROR-CODES.md) · [API usage](../API-USAGE.md) · [Request deadlines and recovery](REQUEST-RECOVERY.md)

Checked against the working-tree implementation on 2026-09-22. This reference applies to REST deployments containing request guards, image input and navigation recovery. Older images may lack codes or fields. Updating documentation does not update a running deployment. MCP tool errors are outside this REST contract.

## 1. Client decision order

The safe-preparation-recovery update adds top-level `request_diagnostics` and may perform bounded read-only navigation recovery before returning a final result. See [scope, budgets, diagnostics and examples](SAFE-RECOVERY.md#english). Clients must still disable automatic replay.

**Use HTTP status to detect transport-level failure, `error.code` to classify it, and submission state to decide whether the original conversation needs inspection. A 500, 504 or timeout does not prove that nothing was sent.**

1. Preserve the HTTP status and read a bounded error body, for example at most 64 KiB.
2. Treat a top-level JSON `error` as failure. SSE may report an error after HTTP 200.
3. Prefer `error.code`; if absent, classify using `error.type` and HTTP status. Do not branch on exact English messages.
4. Strictly validate `send_state` and `prompt_sent`. Missing, malformed or contradictory fields mean an uncertain submission outcome.
5. Disable automatic chat retries in SDKs, job queues and reverse proxies. Guarded chat errors return `automatic_retry_allowed=false`; an absent field does not grant retry permission.
6. Inspect the original result, browser and conversation before explicitly submitting another request. Do not automatically create a replacement conversation on failure.

There is no idempotent submission or failed-request deduplication guarantee. `Retry-After` is a cooldown indication, not permission to replay a prompt.

## 2. Submission state and processing phase

Guarded chat runtime errors include these fields. Early authentication/validation responses, recovery responses and framework errors do not share the complete schema.

| `send_state` | `prompt_sent` | Meaning | Client action |
|---|---|---|---|
| `not_sent` | `false` | No send action was attempted; navigation, text entry or attachment upload may already have occurred | Fix the issue, inspect the page, then let the user decide whether to submit again |
| `confirmed` | `true` | Submission was confirmed; successful reply collection is not implied | Inspect the original conversation instead of duplicating the prompt |
| `unknown` | `null` | A send was attempted without enough evidence to confirm the outcome | Pause further business calls against that browser and inspect the original conversation |

Require actual JSON booleans. Neither `"false"` nor `0` is a valid `false`. Client timeouts, connection loss, non-JSON errors and older responses with missing fields must not be interpreted as `not_sent`.

`phase` records the last processing stage; it does not independently prove submission state:

| Value | Stage |
|---|---|
| `validation` | Parsing, validation, or before later stages |
| `queue` | Waiting for the browser mutation lock |
| `prepare` | Browser or conversation preparation |
| `model_selection` | Selecting the model |
| `navigation` | Opening an explicit or new conversation |
| `input` | Entering text |
| `upload` | Uploading and verifying image attachments |
| `send_ready` | Checking pre-send conditions |
| `send` | A send action has started |
| `reply` | Waiting for, reading and reconciling the reply |

## 3. Chat runtime error reference

HTTP statuses below apply before SSE headers have been sent. `server_error` is usually an `error.type`; **a generic 500 does not necessarily contain `error.code="server_error"`**.

| HTTP | `error.code` | Trigger | Handling |
|---|---|---|---|
| 401 | `invalid_api_key` | Driver detects expired ChatGPT website authentication; this legacy name is misleading | Check website login in the remote desktop, rather than repeatedly replacing the local API key |
| 429 | `rate_limit_exceeded` | Website rate limit | Respect `Retry-After` seconds, stop new tasks and inspect submission state; no automatic replay |
| 500 | **No code**, `type=server_error` | An exception without a dedicated mapping | Inspect submission state, phase and server logs around the same timestamp |
| 502 | `navigation_failed` | Navigation command explicitly reports failure | Inspect the page and `error.navigation`; do not create a replacement conversation |
| 502 | `navigation_displaced` | Target URL was reached, then consecutive probes confirmed displacement | Check login redirects or other page activity; this navigation path has not sent the prompt |
| 503 | `browser_unresponsive` | Browser guard is paused and refuses new operations | Inspect `/health` and the desktop; reconcile previous work before explicit recovery |
| 503 | `lock_timeout` | Browser mutation lock was not obtained within its budget | Wait for active work and check competing clients; no retry loop |
| 503 | `circuit_open` | An error-category circuit breaker is cooling down | Inspect `open_breakers` and the page, resolve the cause and allow cooldown |
| 503 | `owned_tab_required` | Parallel-tab mode lacks a valid owned tab or loses ownership | Administrator checks tab ownership/configuration; do not silently send in another tab |
| 504 | `request_timeout` | Whole-request budget expires; default 120 seconds from queueing through reply collection | Inspect submission state and `phase`; do not assume no submission |
| 504 | `browser_timeout` | An individual browser/CDP operation times out | Check browser responsiveness and pause state; this code alone does not justify restart or replay |
| 504 | `client_disconnected` | Server detects client disconnection and stops the request | Client usually cannot receive this response; inspect the original conversation conservatively |
| 504 | `navigation_timeout` | Explicit conversation fails to become stably ready within its navigation budget, currently 30 seconds | Inspect navigation diagnostics; not sent on this path; an earlier overall expiry may instead produce `request_timeout` |
| 504 | `image_upload_timeout` | Image upload completion was not verified within budget | Check attachment state, size and connectivity; normal upload-failure paths do not submit the prompt |
| 504 | `reply_timeout` | A trustworthy final reply could not be obtained before reconciliation expiry | Inspect the original conversation; the website may already have answered |
| 504 | `generation_stuck` | Website generation appears stalled | Check ongoing generation, network and account state before recovery |

Use the actual tri-state fields, rather than overriding them based on a code. When deadline expiry, disconnection and a paused browser overlap, the guard can prioritize `request_timeout` or `client_disconnected` while retaining the status from the original exception category. Do not require a rigid HTTP/code pairing.

Generic 500 examples include non-timeout image upload failures, some JavaScript/CDP failures, reply reconciliation failures other than deadline expiry, and unexpected runtime exceptions. This is not exhaustive. A 500 alone identifies neither the root cause nor a need to log in again.

## 4. Failures without a dedicated code

| HTTP / form | Current response or cause | Handling |
|---|---|---|
| 401 | `type=auth_error`, `message="Invalid API key"`, no `code` | Check this service's Bearer key separately from ChatGPT website login |
| 400 | `type=invalid_request_error`, no `code` | Correct request structure or image input before submitting again |
| 404 | Unknown route, duplicated `/v1`, unsupported endpoint, or an older image missing a new route | Check Base URL, route and deployment version |
| 405 | Unsupported HTTP method | POST for chat/recovery; GET for query endpoints |
| 413 | Body exceeds the framework's 10 MiB limit | Reduce complete JSON size, including Base64 expansion |
| 500 / non-JSON | Framework exception, failure outside the unified mapper, or intermediary response | Preserve status and a sanitized summary; treat submission outcome as uncertain |
| No HTTP response | Refused connection, client timeout, network or proxy interruption | Distinguish from a server-generated 504; do not assume server work was revoked |

Framework and proxy responses can be plain text or HTML. Not every failure has an `{"error": ...}` body. Validation is not a complete schema for every field type, so malformed requests are not guaranteed to return 400.

### Common 400 causes

- Invalid JSON, a non-object body, `messages` not being a nonempty array of objects, or no user message.
- `new_conversation` not being a JSON boolean; `conversation_id` not being a nonempty string or null; combining `new_conversation=true` with a conversation ID.
- Content is neither text nor an array; invalid content objects; unsupported types other than `text` and `image_url`.
- Images outside the last user message. Use a conversation ID to retain earlier website attachments in follow-ups.
- A non-string `image_url.url`, or `detail` other than `auto`.
- Input is not a PNG, JPEG or WebP Base64 data URL. Remote HTTP URLs and local paths are unsupported.
- Invalid Base64, empty/corrupt images, MIME mismatch or animation.
- More than 4 images, over 4 MiB decoded per image, over 6 MiB decoded in total, or over 20 million pixels per image.

These share 400 + `invalid_request_error`. Codes such as `invalid_image` or `too_many_images` are not currently emitted. See [image input](IMAGE-INPUT.md).

## 5. Navigation diagnostics and example

Synthetic example without real request information:

```json
{
  "error": {
    "code": "navigation_timeout",
    "type": "server_error",
    "message": "Conversation navigation failed: navigation_timeout; stage=composer_unavailable",
    "phase": "navigation",
    "send_state": "not_sent",
    "prompt_sent": false,
    "automatic_retry_allowed": false,
    "navigation": {
      "stage": "composer_unavailable",
      "url_matches": true,
      "ready_state": "interactive",
      "app_shell_present": true,
      "composer_present": true,
      "composer_usable": false,
      "probe_error": null,
      "elapsed_ms": 30000
    }
  }
}
```

`navigation` accompanies explicit-conversation navigation errors. Not every error with `phase=navigation` has this object.

| `navigation.stage` | Meaning |
|---|---|
| `probe_unavailable` | No usable readiness probe result |
| `url_mismatch` | Page URL does not match the requested conversation |
| `document_loading` | Document has not reached an acceptable loading state |
| `app_shell_missing` | Application shell is absent |
| `composer_unavailable` | Composer is absent, hidden or unusable |
| `stability_check` | Conditions are met, but two consecutive stable samples have not been obtained |

`ready_state` is `loading`, `interactive`, `complete` or null; `interactive` alone is not failure. `probe_error` is currently null or `readiness_probe_failed`. `elapsed_ms` measures navigation rather than the complete request. Other fields are booleans. This object contains no real URL, conversation ID, cookie or message text.

## 6. Streaming errors

Before SSE headers, errors can use the HTTP statuses above. After headers, HTTP remains 200 and guarded paths report failures with an event such as:

```text
data: {"error":{"code":"reply_timeout","type":"server_error","message":"Reply deadline exceeded","phase":"reply","send_state":"confirmed","prompt_sent":true,"automatic_retry_allowed":false},"choices":[{"index":0,"delta":{},"finish_reason":"error"}]}

data: [DONE]
```

This is synthetic; actual message wording can differ. A top-level `error` or `finish_reason="error"` means failure. `[DONE]` only terminates the stream. Disconnection or failed error delivery may prevent both the error event and `[DONE]` from arriving.

Require normal completion, no errors and valid business content before accepting success. Legacy compatibility can recognize `[Error: ...]` text without depending on its wording for detailed classification. Start integration with non-streaming calls.

## 7. Administrator recovery endpoint

`POST /v1/browser/recover` uses the local Bearer key and requires no body. It obtains the browser lock and executes a read-only responsiveness probe. Only success clears the pause. Total budget is 5 seconds; probe budget is 3 seconds.

| HTTP | Result | Meaning |
|---|---|---|
| 200 | `status=ready`, `prompt_sent=false` | Probe passed and browser pause cleared; no old request replayed |
| 401 | `type=auth_error`, no code | Invalid local key |
| 503 | `code=browser_busy`, `prompt_sent=false` | Lock not acquired, including lock-resolution failure; active work is not interrupted |
| 503 | `code=browser_unresponsive`, `prompt_sent=false` | Lock acquired but probe failed; pause remains |

Recovery error objects currently contain only `code`, `message` and `prompt_sent`, not the complete chat schema. They describe **the recovery call itself**, not the earlier failed chat.

Recovery does not navigate, restart the service, reconnect the browser or replay prompts. Success proves responsiveness at probe time, not valid authentication, resolution of an earlier request or success of the next generation. It does not clear every independent circuit breaker.

Recommended sequence: pause business calls → inspect health and desktop → reconcile the original conversation → fix page/login if needed → explicitly call recovery → confirm unpaused state → let the user decide the next request. Do not add recovery to an automatic retry loop.

## 8. Health and logging

| Field / value | Interpretation |
|---|---|
| `browser_paused` | Whether REST browser operations are paused |
| `browser_pause_reason=consecutive_cdp_timeouts` | Two consecutive CDP command timeouts |
| `browser_pause_reason=send_outcome_unknown` | Uncertain submission; later operations must not disturb the page |
| `browser_pause_reason=request_interrupted` | Interruption or overall expiry during locked browser work |
| `browser_pause_reason=recovery_probe_failed` | Explicit read-only recovery probe failed |
| `consecutive_cdp_timeouts` | Consecutive timeout count, not all historical errors |
| `last_error_at` | Last recorded error's Unix timestamp in seconds, or null |
| `last_error` | Historical summary, not proof of a continuing failure; can contain sensitive information |
| `open_breakers` | Independent breaker state; inspect alongside website login and the failure cause |
| `requests_served` | Request counter, not successful generation count |

Reading `/health` does not unpause operations. `starting` just after startup need not mean failure; `healthy` does not guarantee the next call. `/v1/models` can return fallback models and project listing can fall back to an empty list, so HTTP 200 on those endpoints does not replace real chat acceptance.

Suggested client logs: a client-generated trace ID, timestamp/timezone, client duration, HTTP status, error code/type, phase, tri-state submission fields and allowlisted navigation diagnostics. A client trace ID does not imply server-side idempotency or request lookup support.

Do not publish Bearer keys, cookies, browser profiles, Base64 images, business prompts, real conversation URLs or complete raw errors. Messages and server logs are not uniformly sanitized; review before sharing. Use synthetic data in public examples.

## 9. Suggested user-facing messages

| Case | Suggested message |
|---|---|
| Invalid input | The request or image is unsupported. Correct it before submitting. |
| Local authentication | API key verification failed. Ask the administrator to check configuration. |
| Website authentication | ChatGPT login has expired. An administrator needs to log in through the remote desktop. |
| Confirmed not sent | This request was not sent. Fix the issue and inspect the page before submitting again. |
| Confirmed sent, collection failed | Your prompt was sent, but the reply could not be collected. Check the original conversation to avoid duplicates. |
| Uncertain submission | Submission could not be confirmed. Check the original conversation first. |
| Browser paused | Browser operations are paused until an administrator checks and recovers the service. |

Unavailable source links, missing citation metadata and model output that fails business JSON validation do not necessarily mean an HTTP failure. Validate and display them separately rather than labeling them all as 500. See [citations](CITATIONS.md).

## 10. Scope and maintenance

Sources: [REST mapping](../src/chatgpt_web2api/api_server.py), [request guard](../src/chatgpt_web2api/request_guard.py), [navigation](../src/chatgpt_web2api/cdp_driver.py), [image validation](../src/chatgpt_web2api/image_input.py).

The current contract still has 400/401/500 responses without codes, some non-JSON errors and legacy naming. This document records existing behavior; it does not introduce new codes. Future schema unification should update implementation, both references and tests, with compatibility changes noted in release documentation.
