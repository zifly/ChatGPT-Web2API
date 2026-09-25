"""Read-only citation check of the current browser chat; prints counts only.

Run inside the service with Python stdin. Does not navigate, send a prompt,
or print cookies, tokens, conversation IDs, reply text or source URLs.
"""
import asyncio
import json
import re
import urllib.request
from urllib.parse import urlsplit

import websockets

from chatgpt_web2api.backend_projection import CONVERSATION_PROJECTION_JS
from chatgpt_web2api.citations import annotations_for_node


async def main():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:9222/json/list', timeout=5) as response:
        pages = json.load(response)
    pages = [page for page in pages if page.get('type') == 'page'
             and urlsplit(page.get('url', '')).hostname == 'chatgpt.com'
             and '/c/' in urlsplit(page.get('url', '')).path]
    print(json.dumps({'conversation_tabs': len(pages), 'read_only': True}))
    for index, page in enumerate(pages):
        expression = r"""(async () => {
          const conv_id = location.pathname.match(/\/c\/([^/]+)/)?.[1];
          if (!conv_id) return JSON.stringify({__status: 'no_conversation'});
          const session = await fetch('/api/auth/session', {signal: AbortSignal.timeout(10000)});
          if (!session.ok) return JSON.stringify({__status: 'session_unavailable'});
          const token = (await session.json()).accessToken;
          if (!token) return JSON.stringify({__status: 'login_required'});
          const __D = {conv_id, token, limit: 50};
          return await __PROJECTION__;
        })()""".replace('__PROJECTION__', CONVERSATION_PROJECTION_JS)
        try:
            async with asyncio.timeout(35):
                async with websockets.connect(page['webSocketDebuggerUrl'], max_size=8_000_000) as ws:
                    await ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {
                        'expression': expression, 'awaitPromise': True, 'returnByValue': True, 'timeout': 30000,
                    }}))
                    while True:
                        reply = json.loads(await ws.recv())
                        if reply.get('id') == 1:
                            break
            projection = json.loads(reply['result']['result']['value'])
            if 'nodes' not in projection:
                print(json.dumps({'tab_index': index, 'status': 'conversation_read_failed'}))
                continue
            nodes = projection['nodes']
            current = projection.get('current_node')
            visited = set()
            node = None
            while current and current in nodes and current not in visited:
                visited.add(current)
                candidate = nodes[current]
                if candidate.get('role') == 'assistant' and candidate.get('end_turn') and candidate.get('text'):
                    node = candidate
                    break
                current = candidate.get('parent')
            if not node:
                print(json.dumps({'tab_index': index, 'status': 'no_completed_text_on_current_branch'}))
                continue
            text = node['text']
            markers = re.findall(r'\ue200cite\ue202([^\ue201\r\n]+)\ue201', text)
            refs = {ref for marker in markers for ref in marker.split('\ue202') if ref}
            annotations = annotations_for_node(node, text)
            print(json.dumps({'tab_index': index, 'status': 'read_complete',
                              'cited_reference_count': len(refs),
                              'metadata_group_count': len(node.get('citation_references', [])),
                              'resolved_reference_count': len(annotations),
                              'unresolved_reference_count': len(refs) - len(annotations)}))
        except Exception as error:
            print(json.dumps({'tab_index': index, 'error_type': type(error).__name__}))


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as error:
        print(json.dumps({'diagnostic_error_type': type(error).__name__}))
        raise SystemExit(1)
