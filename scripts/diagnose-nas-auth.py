"""Read browser status without printing cookie values, tokens or page content."""
import asyncio
import json
import urllib.request
from urllib.parse import urlsplit

import websockets


async def main():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:9222/json/list", timeout=5) as response:
        pages = json.load(response)
    for page in pages:
        if page.get("type") != "page":
            continue
        url = urlsplit(page.get("url", ""))
        print("Page origin:", f"{url.scheme}://{url.netloc}")
        async with websockets.connect(page["webSocketDebuggerUrl"]) as ws:
            expression = """(async () => {
                const text = (document.body?.innerText || '').toLowerCase();
                const title = document.title.toLowerCase();
                const info = {
                    readyState: document.readyState,
                    challenge: /just a moment|verify you are human|checking your browser/.test(text + title),
                    browserError: location.protocol === 'chrome-error:',
                    proxyError: /err_proxy|err_tunnel/.test(text),
                    connectionError: /err_connection|err_name_not_resolved|err_timed_out/.test(text)
                };
                try {
                    const r = await fetch('https://chatgpt.com/api/auth/session', {
                        credentials: 'include', signal: AbortSignal.timeout(10000)
                    });
                    info.status = r.status;
                    info.contentType = r.headers.get('content-type');
                    info.server = r.headers.get('server');
                    info.mitigated = r.headers.get('cf-mitigated');
                    const body = await r.text();
                    info.responseChallenge = /challenge-platform|cf-chl-|just a moment/i.test(body);
                    if (r.ok && info.contentType?.includes('application/json')) {
                        try {
                            const token = JSON.parse(body).accessToken;
                            info.hasAccessToken = !!token;
                            const match = location.pathname.match(/\/c\/([^/]+)/);
                            info.userMessageCount = document.querySelectorAll('[data-message-author-role="user"]').length;
                            info.assistantMessageCount = document.querySelectorAll('[data-message-author-role="assistant"]').length;
                            info.pageReportsError = /something went wrong|unable to load|出了点问题/.test(text);
                            if (match && token) {
                                const id = decodeURIComponent(match[1]);
                                info.conversationId = id;
                                info.conversationChecks = [];
                                const candidates = id.startsWith('WEB:') ? [id, id.slice(4)] : [id];
                                for (const candidate of candidates) {
                                    const check = await fetch('/backend-api/conversation/' + encodeURIComponent(candidate) + '?offset=0&limit=50', {
                                        headers: {Authorization: 'Bearer ' + token}, signal: AbortSignal.timeout(5000)
                                    });
                                    const item = {id: candidate, status: check.status};
                                    if (check.ok) {
                                        const data = await check.json();
                                        item.nodeCount = Object.keys(data.mapping || {}).length;
                                    }
                                    info.conversationChecks.push(item);
                                }
                            }
                        } catch (_) { info.conversationCheckFailed = true; }
                    }
                } catch (_) { info.fetchFailed = true; }
                return JSON.stringify(info);
            })()"""
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
                "expression": expression, "awaitPromise": True, "returnByValue": True,
                "timeout": 30000,
            }}))
            async with asyncio.timeout(35):
                while True:
                    result = json.loads(await ws.recv())
                    if result.get("id") != 1:
                        continue
                    value = result.get("result", {}).get("result", {}).get("value")
                    if isinstance(value, str):
                        # Print only the explicit diagnostic fields above.
                        print(json.dumps(json.loads(value), ensure_ascii=False))
                    else:
                        print("Browser evaluation failed; no page content printed")
                    break


asyncio.run(main())
