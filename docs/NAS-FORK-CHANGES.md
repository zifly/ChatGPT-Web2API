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

146 related regressions and an exact local synthetic JSON request passed. Two NAS rounds over three fixed samples produced six requests/ten work-group results passing structure and candidate checks; the second round used the review UI. Same-turn protocol-original comparisons, broader concurrency and streaming acceptance remain outstanding. Formal library associations were unchanged; see README for validation limits.

Complete Chinese/English installation and API guides include mode configuration, SSE behavior, acceptance limits and stdin diagnostics for images missing the check script. The noVNC root URL opens the desktop client directly.
