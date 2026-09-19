# Security Policy

## Reporting a Vulnerability

Do not publish credentials, session data, private conversations or exploitable vulnerability details in an issue or pull request.

Use this repository's Security tab to check whether private vulnerability reporting is available. If no private reporting channel is offered, request one without disclosing sensitive details. For an issue in unchanged upstream code, check the [upstream security policy](https://github.com/Octo-Lex/ChatGPT-Web2API/security/policy).

A private report should describe the affected version, reproduction steps and potential impact, with secrets removed. This community fork does not promise a security response time or independent support for upstream releases.

## Data and Network Boundaries

This project controls a Chrome browser signed into your ChatGPT account:

- Your client sends requests to the local/NAS API service. The browser then connects to ChatGPT/OpenAI services and sends prompts and account authentication data as required by those services. Traffic is not confined to your local computer.
- A configured network proxy also carries browser traffic. Its visibility depends on the proxy and transport configuration; configure only infrastructure you trust.
- ChatGPT cookies and other browser session data are stored in `data/chrome-profile/`. Optional imported cookies are in `data/cookies/cookies.json`. The NAS API key and VNC password are stored in `data/api.env` and `data/vnc-password.txt` respectively. These files and their backups are sensitive and must remain private.
- Runtime and diagnostic logs may contain prompts, conversation identifiers or environment details. Review and redact logs before sharing them.

## NAS Listener Defaults

The Compose configuration publishes API port `11111` and desktop port `6080` on host `127.0.0.1` by default. Setting `W2A_BIND_ADDRESS` to the NAS LAN IP makes those ports reachable on that interface, subject to the host firewall. Inside the API container, the service binds to `0.0.0.0:8080`; host publishing and container listening are separate settings.

The supplied endpoints use HTTP and do not provide TLS themselves. Use a trusted network and appropriate access controls. Do not expose the logged-in desktop or Chrome debugging port to the public internet. Traditional VNC authentication uses only the first eight password characters and should not be treated as a public-facing security boundary.

## Publishing and Backups

Never upload `data/`, browser profiles, cookie exports, actual `.env` files, logs or account backups. `.gitignore` and `.dockerignore` reduce accidental inclusion but do not remove files already committed or secrets stored in old Git history. Audit the actual commit or archive before publication.

This fork retains upstream history. Sanitizing the current file tree does not sanitize historical upstream captures. The NAS credentials used during local validation are not part of the published source.
