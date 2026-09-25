"""Exercise upload selectors on synthetic modern/legacy DOM in an owned blank tab.

Requires local Chrome CDP. Never opens ChatGPT or sends a prompt; closes only
the blank tab it creates. Run inside the container using stdin.
"""
import asyncio
import json
import urllib.request

import websockets

from chatgpt_web2api.image_upload import CLEANUP_JS, FILE_INPUT_JS, UPLOAD_STATE_JS


async def main():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    version = json.load(opener.open('http://127.0.0.1:9222/json/version', timeout=5))
    async with websockets.connect(version['webSocketDebuggerUrl']) as browser:
        sequence = 0

        async def browser_call(method, params):
            nonlocal sequence
            sequence += 1
            await browser.send(json.dumps({'id': sequence, 'method': method, 'params': params}))
            while True:
                message = json.loads(await browser.recv())
                if message.get('id') == sequence:
                    assert 'error' not in message, message.get('error')
                    return message.get('result', {})

        target = (await browser_call('Target.createTarget', {'url': 'about:blank'}))['targetId']
        try:
            pages = json.load(opener.open('http://127.0.0.1:9222/json/list', timeout=5))
            page = next(p for p in pages if p['id'] == target)
            async with websockets.connect(page['webSocketDebuggerUrl']) as ws:
                async def evaluate(expression):
                    await ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate',
                                              'params': {'expression': expression, 'returnByValue': True}}))
                    while True:
                        result = json.loads(await ws.recv())
                        if result.get('id') == 1:
                            assert 'error' not in result, result.get('error')
                            body = result['result']
                            assert 'exceptionDetails' not in body, body.get('exceptionDetails')
                            return body['result'].get('value')

                async def load(html):
                    await evaluate('document.body.innerHTML = ' + json.dumps(html))

                async def state(names, uploaded=None):
                    return json.loads(await evaluate(
                        '(function(__D){return ' + UPLOAD_STATE_JS + ';})('
                        + json.dumps({'names': names, 'uploaded': uploaded or []}) + ')'))

                # Modern page: hidden legacy fallback must not steal the form.
                await load('''<form><textarea id="prompt-textarea" hidden></textarea></form>
                    <form id="active"><div role="textbox" class="ProseMirror" contenteditable="true"></div>
                    <input type="file" accept="image/*,video/*"><input id="photo" type="file" accept="image/*">
                    <input type="file"><button type="submit" aria-label="发送">Send</button></form>''')
                assert await evaluate('(' + FILE_INPUT_JS + ')?.id') == 'photo'
                assert (await state([]))['sendReady']
                await evaluate("document.querySelector('#active button').disabled=true")
                assert not (await state([]))['sendReady']

                # Unrelated form input cannot be selected; duplicate image
                # candidates fail closed instead of uploading to a guessed input.
                await evaluate("document.querySelector('#photo').remove()")
                assert await evaluate('(' + FILE_INPUT_JS + ') === null')
                await evaluate("document.querySelector('#active').insertAdjacentHTML('beforeend',"
                               "'<input type=file accept=image/*><input type=file accept=image/*>')")
                assert await evaluate('(' + FILE_INPUT_JS + ') === null')

                for role in ('group', 'button'):
                    composer = ('<textarea id="prompt-textarea"></textarea>' if role == 'group'
                                else '<div role="textbox" class="ProseMirror" contenteditable="true"></div>')
                    await load('<form>' + composer + '''<input id="upload-photos" type="file">
                        <button type="submit" data-testid="send-button">Send</button>'''
                        + ''.join(f'''<div role="{role}" aria-label="{name}">
                            <img src="data:image/png;base64,AA==">
                            <button aria-label="移除 {name}" onclick="this.parentElement.remove()">Remove</button>
                            </div>''' for name in ('owned.png', 'foreign.png')) + '</form>')
                    assert await evaluate('(' + FILE_INPUT_JS + ')?.id') == 'upload-photos'
                    await evaluate("document.querySelectorAll('img').forEach(img => {"
                                   "Object.defineProperty(img,'complete',{value:true});"
                                   "Object.defineProperty(img,'naturalWidth',{value:2});})")
                    observed = await state(['owned.png'])
                    assert set(observed['names']) == {'owned.png', 'foreign.png'}
                    assert not observed['ready'], 'a local data preview must not confirm upload'
                    assert not (await state(['owned.png'], ['foreign.png']))['ready']
                    assert (await state(['owned.png'], ['owned.png']))['ready']
                    await evaluate('(function(__D){return ' + CLEANUP_JS + ';})({names:["owned.png"]})')
                    assert (await state([]))['names'] == ['foreign.png']

                await load('<div>No composer</div>')
                assert (await state([]))['missing']
                print('PASS: modern/legacy forms, file-input scope, ambiguity, preview guard and exact cleanup')
        finally:
            await browser_call('Target.closeTarget', {'targetId': target})


if __name__ == '__main__':
    asyncio.run(main())
