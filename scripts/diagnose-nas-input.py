"""Read-only composer diagnostics; never prints messages, IDs, filenames or URLs."""
import asyncio
import hashlib
import json
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
import websockets

PROBE = r"""(() => {
  const composer=document.querySelector('#prompt-textarea');
  const form=composer?.closest('form');
  const tiles=form ? [...form.querySelectorAll('[role="group"][aria-label]')].filter(e => e.querySelector('img') || [...e.querySelectorAll('button[aria-label]')].some(b => (b.getAttribute('aria-label') || '').includes(e.getAttribute('aria-label')))) : [];
  const send=form?.querySelector('[data-testid="send-button"]');
  return {
    documentState:document.readyState,
    conversationRoute:/\/c\/[^/]+/.test(location.pathname),
    appShell:!!document.querySelector('nav, [class*="sidebar"]'),
    composer:!!composer, form:!!form,
    uploadInput:!!document.querySelector('#upload-photos, input[data-testid="upload-photos-input"]'),
    composerHasText:!!composer?.textContent?.trim(),
    sendPresent:!!send, sendEnabled:!!send && !send.disabled && send.getAttribute('aria-disabled')!=='true',
    attachmentCount:tiles.length,
    attachments:tiles.map(tile => {
      const img=tile.querySelector('img');
      let preview='none';
      if(img) {try {const u=new URL(img.src,location.href); preview=u.protocol==='blob:'?'blob':u.protocol==='data:'?'data':u.origin===location.origin?(u.pathname.startsWith('/backend-api/')?'same-origin-backend':'same-origin-other'):'other-origin';} catch(_) {preview='invalid';}}
      return {preview,decoded:!!img && img.complete && img.naturalWidth>0,progress:!!tile.querySelector('[role="progressbar"], [aria-busy="true"]'),alert:!!tile.querySelector('[role="alert"]')};
    })
  };
})()"""

async def main():
    import chatgpt_web2api.api_server as api
    source=Path(api.__file__).parent
    print(json.dumps({'loaded_source_sha256':{name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in ['api_server.py','cdp_driver.py','image_upload.py']}}))
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:9222/json/list',timeout=5) as r: pages=json.load(r)
    selected=[p for p in pages if p.get('type')=='page' and urlsplit(p.get('url','')).hostname=='chatgpt.com']
    print(json.dumps({'chatgpt_tabs':len(selected)}))
    for i,p in enumerate(selected):
        try:
            async with asyncio.timeout(10):
                async with websockets.connect(p['webSocketDebuggerUrl']) as ws:
                    await ws.send(json.dumps({'id':1,'method':'Runtime.evaluate','params':{'expression':PROBE,'returnByValue':True,'timeout':5000}}))
                    while True:
                        result=json.loads(await ws.recv())
                        if result.get('id')!=1: continue
                        value=result.get('result',{}).get('result',{}).get('value')
                        print(json.dumps({'tab_index':i,'state':value if isinstance(value,dict) else 'probe_failed'}))
                        break
        except Exception as e: print(json.dumps({'tab_index':i,'error_type':type(e).__name__}))

if __name__=='__main__':
    try: asyncio.run(main())
    except Exception as e: print(json.dumps({'diagnostic_error_type':type(e).__name__})); raise SystemExit(1)
