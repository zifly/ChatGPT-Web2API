# NAS fork changes

Baseline: Octo-Lex/ChatGPT-Web2API commit 497527dceabfa3f95961e23c291e618c5570f1ac.
The API/MCP foundation is upstream work. Changes cover Docker Chrome installation, desktop startup, cookie import validation, temporary conversation IDs, guarded fresh-chat replies, NAS scripts and Chinese documentation.

Publication changes: configurable proxy/bind address, fixed base image name, loopback defaults, generic installation guide. Private data, logs, sentinel captures and GitHub automation are excluded from this candidate. No local Git history is copied.

Publishing into the user's fork should apply reviewed changes on top of upstream history; do not force-replace the fork history or upload the NAS folder wholesale. Removing upstream automation/capture files should be an explicit part of the publication diff.

No new attachment support, extra account allowances or fully validated parallel-client behavior is claimed. Unit tests cannot replace a live deployment check.

Publication review v2 excludes historical capture/experiment scripts, protocol notes and upstream marketing images/docs outside the NAS/API documentation scope. Conversation fixture identifiers and timestamps are synthetic; structure and text semantics are retained. These omissions must be explicit when applying changes to an upstream fork.

## 2026-09-20 installation follow-up

Official package sources/no proxy remain the defaults; optional mirror and build/runtime proxy settings are independent. TUNA mirror builds and an isolated Docker Desktop startup/login/text request passed. Login waits can be indefinite in desktop mode, shutdown interrupts the wait, and stale Xvfb locks are checked on startup. Three regression tests cover login detection, finite timeout and shutdown. No private test profiles, keys or logs are included.


## 2026-09-20 reply integrity and protocol mode

The upstream baseline sliced DOM deltas using the previous snapshot length, and non-streaming concatenated them. Prefix rewrites could corrupt output; final suffix reconciliation could not repair it. This fork buffers progress and returns the entire correlated final text, retaining upstream turn anchors.

Opt-in `W2A_REPLY_SOURCE=backend` bypasses assistant DOM text/completion and polls the authenticated conversation protocol for the anchored terminal reply. Browser login and sending remain necessary. It fails without a trustworthy final reply and never falls back to page text. Both modes buffer SSE content until final verification. The default remains `reconciled`; the tested NAS explicitly uses `backend`.

The earlier reader passed 146 related regressions and an exact local synthetic JSON request. Public validation uses synthetic inputs only; downstream application examples and results are omitted. Broader long-context and concurrency acceptance remain outstanding.

Complete Chinese/English installation and API guides include mode configuration, SSE behavior, acceptance limits and stdin diagnostics for images missing the check script. The noVNC root URL opens the desktop client directly.

## 2026-09-20 image input

REST Chat Completions accepts bounded PNG/JPEG/WebP Base64 data URLs in the last user message. Image bytes are validated before browser mutation; CDP uploads through the webpage file input and waits for server-hosted previews before sending. Multimodal user text is preserved for turn correlation. Unconfirmed uploads fail without sending; image requests bypass the non-streaming rate-limit resend wrapper. The default text path is retained. No generated-image downloads, document uploads or MCP image parameters are added.

Single-image non-streaming, two-image SSE and a text-only follow-up passed on an isolated Docker Desktop browser. The same features subsequently passed synthetic NAS acceptance; see [image input](IMAGE-INPUT.md) for limits, examples and evidence boundaries.

## 2026-09-20 client-controlled conversations

Added top-level `new_conversation: true` to force a new webpage chat independently of system messages, for text and image requests. `conversation_id` continues the specified chat. Conflicting controls and invalid types return HTTP 400 before browser mutation; omission/false preserves legacy behavior. Eighteen HTTP regression cases cover both response formats, image routing, explicit-ID switching, failures and compatibility. NAS deployment subsequently passed a real new-chat / image-chat / SSE follow-up / switch-back sequence; see the image guide.

## 2026-09-21 web citation sources / 网页引用来源

The conversation projection now retains a bounded set of web reference fields from terminal assistant text messages. The anchored reply carries unambiguous sources through to REST `message.annotations` or SSE `delta.annotations`, without changing its text. Client applications must forward and render these fields. Unsupported or missing metadata stays unresolved; sources from unrelated turns are never substituted, and old client records are not updated automatically. See the [bilingual citation guide](CITATIONS.md).

本次补充的是引用数据传递：保留本轮回复的来源编号、标题和网址，由客户端显示可点击链接。正文保持原样；缺失或有歧义的来源不猜测，已保存的旧回复不自动修复。

The update passed 184 related offline tests. Real NAS citation extraction and end-to-end display still need live acceptance. 此功能的真实 NAS 网页验收尚未完成。

## 2026-09-21 REST request deadlines / 请求截止时间与浏览器暂停

REST requests now share a single deadline across queueing, navigation, input, uploads and reply reading. A detected disconnect cancels remaining work. Error responses report the failed phase and whether submission is absent, confirmed or unknown; post-click uncertainty must not trigger automatic replay. Cancellation also removes pending CDP waiters and skips page cleanup after an abandoned image request.

Consecutive CDP command timeouts pause this REST process's browser operations and downgrade health. An authenticated, locked, read-only recovery probe can clear the pause without navigation, reconnect, restart or replay. See [recovery contract](REQUEST-RECOVERY.md). The combined citation/guard suite passed 250 offline tests; real NAS acceptance is pending.

整次请求共用截止时间；点击发送后未收到确认会明确标记“不确定”。连续浏览器超时后暂停接收页面操作，并提供只读恢复检查。这是错误处理与恢复行为的修复，尚未证明网页卡顿的资源或网络根因已经解决。

## 2026-09-25 REST conversation concurrency / REST 多会话并发

Added a bounded pool of independent REST browser workers. NAS Compose enables
two slots and at most 32 queued requests; ordinary installs retain the singleton
default. Different conversations overlap, the same ID is serialized within one
REST process, and queue wait remains inside the existing request deadline.
Cancelled waiters cannot send later. Each worker has its own guard and breaker
state; unresolved turns retain their conversation binding until explicit recovery.
Account rate limits pause new work pool-wide. Pooled requests without an ID always
start a fresh chat, removing the singleton's implicit last-conversation routing.

The JSON/SSE success contract is unchanged. Added pool health diagnostics and
optional `slot` selection on the existing recovery endpoint. See the
[frontend/backend migration checklist](REST-CONCURRENCY.md). The related suite
passed 221 offline tests, including 23 new pool cases and 26 upload/acknowledgment cases. Missing shared reply/image
test fixtures and stale navigation/timeout test expectations were repaired so
the existing protocol, citation and cancellation regressions can run.

Live concurrency acceptance exposed image selectors left on the old webpage
layout. The uploader now shares the current composer selectors, supports both
attachment card roles and the scoped image-only file input, and verifies local
previews against exact-filename/file-ID server processing completion. Mere HTTP
200 or a local thumbnail cannot authorize sending. Network handlers are restored
on success, failure or cancellation, and a reconnect invalidates the capture.
An isolated real NAS upload and cleanup passed without sending a prompt.

Fresh-chat protocol reads now follow at most three provisional route changes only when the outgoing user UUID was captured and no existing conversation was specified. The first backend match for that exact user node pins the conversation. Existing conversation IDs and uncaptured sends keep strict route checks; identical prompt text never substitutes for the captured UUID. Five additional protocol cases and 34 turn-selector cases passed.

The rebuilt NAS deployment passed four live synthetic requests: parallel new text and blue-image conversations (40.08 / 35.28 s), then parallel explicit-ID JSON and SSE follow-ups (6.52 / 6.77 s). IDs remained distinct and stable, each reply recalled its own code, and SSE ended with stop and DONE. Observed peak active workers: 2. See the [acceptance record](REST-CONCURRENCY.md).

## 2026-09-25 New-conversation replacement retries / 放弃旧会话后重试

Gateways may abandon a failed attempt and automatically retry once in a new conversation, even after an uncertain or confirmed submission. JSON/SSE errors advertise `new_conversation_retry` with an allowed flag, one-retry budget and cooldown; the legacy flag still forbids ordinary request replay. Gateways enforce the task-wide budget, rebuild context/images and atomically reject old-attempt events. The driver still sends at most once per request; shared cooldown and worker quarantine remain in effect. The related suite passed 235 tests, including 14 new replacement-policy cases. See [gateway requirements](NEW-CONVERSATION-RETRY.md).

The NAS builder does not support a build-time `host-gateway` mapping. Removed that optional build mapping and documented using a reachable LAN address for build proxies; runtime host mapping is unchanged.
