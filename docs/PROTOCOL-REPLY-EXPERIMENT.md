# Experimental backend reply mode

Enable `W2A_REPLY_SOURCE=backend` in the process environment, or in `.env` for Docker Compose. Default `reconciled` retains the reviewed buffered-reply path. This mode includes the reply-integrity fix and was deployed on the test NAS on 2026-09-20. It remains opt-in in the repository configuration.

This is browser-assisted protocol reading, not a browser-free HTTP client. The browser still logs in, selects the model, opens the conversation and sends the user message. After send acknowledgment, the driver polls the existing authenticated `/backend-api/conversation/{id}` reader and resolves the current turn using the captured user-message UUID or the existing conservative fallback anchor. Completion requires a matched terminal text node with `end_turn=true`.

The experimental reader never polls assistant DOM text, never uses DOM completion buttons, and never falls back to a rendered-page answer. Missing server conversation IDs (including persistent WEB placeholders), empty/partial/non-text replies, ambiguous matches, auth failures and deadlines do not become successful text replies. Existing or already anchored conversation IDs cannot change. A fresh chat with a captured outgoing user UUID may follow at most three provisional URL changes until the exact submitted user node appears in the backend; it then pins that conversation. Without that UUID, changed IDs still fail. No extra send is issued by the reader; only backend reads are repeated. Existing upstream send/transport retry behavior elsewhere is unchanged.

Both REST response modes receive the completed protocol text; SSE content remains buffered rather than token-by-token. Structured model output still requires consumer-side validation. The default timeout is supplied by the caller; the backend polling phase has a bounded deadline including in-flight reads. REST requests share one overall deadline across queueing, navigation, upload, send acknowledgment and reply reading; individual operations cannot extend that budget.

Validation: 146 related offline tests passed. A real isolated Chrome instance fetched a local synthetic conversation endpoint; actual projection JavaScript and turn selection returned exact JSON, Unicode, escaped characters and whitespace while ignoring intentionally incorrect DOM text. These tests do not prove current ChatGPT protocol compatibility; live acceptance is recorded separately outside the repository.


Live follow-up: one local synthetic JSON request was exactly preserved. Subsequent synthetic NAS checks passed for text, image input, SSE follow-up and explicit conversation switching. See [image validation](IMAGE-INPUT.md), [English installation](../NAS-INSTALLATION.md) and [English API guide](../API-USAGE.md). No downstream application instances or private test inputs are published.

2026-09-25: the REST pool and captured-UUID route handling passed the [two-conversation NAS acceptance](REST-CONCURRENCY.md), including concurrent text/image sends and isolated JSON/SSE continuations.
