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

Consecutive CDP command timeouts pause this REST process's browser operations and downgrade health. An authenticated, locked, read-only recovery probe can clear the pause without navigation, reconnect, restart or replay. See [recovery contract](REQUEST-RECOVERY.md). The initial combined citation/guard suite passed 250 offline tests; updated validation is recorded below.

整次请求共用截止时间；点击发送后未收到确认会明确标记“不确定”。连续浏览器超时后暂停接收页面操作，并提供只读恢复检查。这是错误处理与恢复行为的修复，尚未证明网页卡顿的资源或网络根因已经解决。

## 2026-09-22 safe preparation recovery / 安全准备恢复

Explicit-conversation, non-streaming REST requests can perform bounded read-only navigation readiness recovery, with at most three preparation attempts, 5/15-second backoffs and the original overall deadline. Recovery requires unchanged page ownership, no draft/attachments/generation and no uncertain transport operation. It never repeats navigation, input, upload or sending. REST rate limits and socket failures no longer replay their chat/command operations. Responses include bounded request diagnostics. See [the bilingual contract and synthetic examples](SAFE-RECOVERY.md), [Chinese error reference](ERROR-CODES.md) and [English error reference](ERROR-CODES.en.md).

指定会话的非流式请求可在原预算内有限重新确认页面就绪；无法证明现场安全时停止，不能重复发送。新增恢复诊断、中英文错误码手册，并补充网页登录失效、停用旧 Cookie 的安装排查步骤。

352 related offline tests passed. Four sequential synthetic NAS chats passed for text, image input and their explicit-ID continuations, with one client POST each and valid diagnostics. Recovery counts were zero; normal-path live acceptance does not prove recovery under a real webpage fault. No business prompts, request identifiers or private integration records are included in this repository.
