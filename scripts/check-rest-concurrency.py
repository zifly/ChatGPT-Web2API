"""Opt-in live acceptance: two new synthetic chats, then two isolated follow-ups.

Run inside the NAS container with --send. Creates two account conversations;
does not delete history or retry sends. Reads the API key from the environment
and prints only test results, never credentials or arbitrary reply text.
"""
import argparse
import asyncio
import base64
import io
import json
import os
import time
import uuid

import aiohttp
from PIL import Image


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send', action='store_true')
    args = parser.parse_args()
    if not args.send:
        parser.error('Pass --send to create two synthetic chats and send four requests')
    key = os.environ.get('W2A_API_KEYS', '').split(',')[0].strip()
    if not key:
        raise RuntimeError('W2A_API_KEYS must be configured')
    base = 'http://127.0.0.1:8080'
    headers = {'Authorization': 'Bearer ' + key}
    trace = {'peak_active': 0, 'requests': []}
    codes = ['POOL-A-' + uuid.uuid4().hex[:8], 'POOL-B-' + uuid.uuid4().hex[:8]]
    picture = io.BytesIO()
    Image.new('RGB', (320, 200), 'blue').save(picture, format='PNG')
    data_url = 'data:image/png;base64,' + base64.b64encode(picture.getvalue()).decode()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as client:
        async def monitor():
            while True:
                async with client.get(base + '/health') as response:
                    body = await response.json()
                pool = body['rest_pool']
                assert pool['enabled'] and pool['size'] == 2
                trace['peak_active'] = max(trace['peak_active'], pool['active'])
                await asyncio.sleep(0.2)

        async def turn(index, cid=None, stream=False):
            text = (f'Remember this synthetic test code: {codes[index]}. Reply with exactly that code.'
                    if cid is None else 'What test code did I ask you to remember? Reply with exactly that code.')
            content = text
            if index == 1 and cid is None:
                text = (f'Remember this synthetic test code: {codes[index]}. Inspect the attached image. '
                        'If it is solid blue, reply with exactly that code; otherwise reply COLOR_MISMATCH.')
                content = [{'type': 'text', 'text': text},
                           {'type': 'image_url', 'image_url': {'url': data_url}}]
            payload = {'model': 'auto', 'stream': stream,
                       'messages': [{'role': 'user', 'content': content}]}
            payload.update({'conversation_id': cid} if cid else {'new_conversation': True})
            started = time.monotonic()
            async with client.post(base + '/v1/chat/completions', json=payload, headers=headers) as response:
                wire = await response.text()
                if response.status != 200:
                    error = json.loads(wire).get('error', {})
                    raise RuntimeError(f"HTTP {response.status}: {error.get('code')}; phase={error.get('phase')}; send_state={error.get('send_state')}")
                if stream:
                    events = [json.loads(line[6:]) for line in wire.splitlines()
                              if line.startswith('data: {')]
                    if any('error' in e for e in events):
                        raise RuntimeError('SSE error; inspect the service without replaying this request')
                    stop = next(e for e in events if e['choices'][0]['finish_reason'] == 'stop')
                    assert wire.endswith('data: [DONE]\n\n')
                    result_id = stop['conversation_id']
                    result_text = ''.join(e['choices'][0]['delta'].get('content', '') for e in events)
                else:
                    result = json.loads(wire)
                    assert result['choices'][0]['finish_reason'] == 'stop'
                    result_id = result['conversation_id']
                    result_text = result['choices'][0]['message']['content']
                assert result_id and (cid is None or result_id == cid), 'conversation routing failed'
                assert result_text.strip() == codes[index], 'synthetic reply did not match its own code'
            trace['requests'].append({'session': 'AB'[index], 'follow_up': bool(cid),
                                      'stream': stream, 'seconds': round(time.monotonic() - started, 2),
                                      'ok': True})
            return result_id

        watcher = asyncio.create_task(monitor())
        async def pair(*requests):
            results = await asyncio.gather(*requests, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
            return results

        try:
            ids = await pair(turn(0), turn(1))
            assert ids[0] != ids[1], 'new conversations must differ'
            await pair(turn(0, ids[0]), turn(1, ids[1], stream=True))
            assert trace['peak_active'] == 2, 'no overlap was observed'
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        print(json.dumps(trace, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
