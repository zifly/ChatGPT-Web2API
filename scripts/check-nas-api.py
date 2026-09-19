"""Run inside the container; --send explicitly sends one test conversation."""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="Send one 'Reply with exactly: OK' request")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    key = os.environ.get("W2A_API_KEYS", "").split(",")[0].strip()
    if not key:
        print("FAIL: W2A_API_KEYS is not configured")
        return 1
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(args.base_url.rstrip("/") + path, data=data, headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json",
        })
        with opener.open(req, timeout=180 if payload is not None else 20) as response:
            return json.load(response)

    try:
        health = request("/health")
        print("Health:", json.dumps({name: health.get(name) for name in (
            "status", "chrome_running", "driver_connected", "requests_served",
            "last_successful_send_at", "last_error", "open_breakers",
        )}, ensure_ascii=False))
        if not health.get("chrome_running") or not health.get("driver_connected"):
            print("FAIL: browser or driver is not connected")
            return 1
        if health.get("open_breakers"):
            print("FAIL: an active circuit breaker requires diagnosis")
            return 1
        models = request("/v1/models").get("data", [])
        print("Models:", len(models))
        if not models:
            print("FAIL: model list is empty")
            return 1
        if not args.send:
            print("Read-only checks passed. Chat generation has NOT been tested.")
            return 0
        print("Sending ONE test request; do not resend automatically if this times out.", flush=True)
        reply = request("/v1/chat/completions", {
            "model": "auto", "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "stream": False,
        })
        choices = reply.get("choices") or []
        text = choices[0].get("message", {}).get("content") if choices else None
        if not isinstance(text, str) or text.strip() != "OK":
            print("FAIL: API did not return the expected OK (HTTP success alone is insufficient)")
            return 1
        print("PASS: chat API returned OK")
        print("Conversation ID:", reply.get("conversation_id") or "not persisted / unavailable")
        return 0
    except urllib.error.HTTPError as exc:
        print(f"FAIL: HTTP {exc.code}; inspect runtime.log for the cause")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"FAIL: {type(exc).__name__}; inspect runtime.log and the browser before retrying")
    return 1


if __name__ == "__main__":
    sys.exit(main())
