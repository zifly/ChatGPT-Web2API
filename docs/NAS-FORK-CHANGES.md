# NAS fork changes

Baseline: Octo-Lex/ChatGPT-Web2API commit 497527dceabfa3f95961e23c291e618c5570f1ac.
The API/MCP foundation is upstream work. Changes cover Docker Chrome installation, desktop startup, cookie import validation, temporary conversation IDs, guarded fresh-chat replies, NAS scripts and Chinese documentation.

Publication changes: configurable proxy/bind address, fixed base image name, loopback defaults, generic installation guide. Private data, logs, sentinel captures and GitHub automation are excluded from this candidate. No local Git history is copied.

Publishing into the user's fork should apply reviewed changes on top of upstream history; do not force-replace the fork history or upload the NAS folder wholesale. Removing upstream automation/capture files should be an explicit part of the publication diff.

No new attachment support, extra account allowances or fully validated parallel-client behavior is claimed. Unit tests cannot replace a live deployment check.

Publication review v2 excludes historical capture/experiment scripts, protocol notes and upstream marketing images/docs outside the NAS/API documentation scope. Conversation fixture identifiers and timestamps are synthetic; structure and text semantics are retained. These omissions must be explicit when applying changes to an upstream fork.
