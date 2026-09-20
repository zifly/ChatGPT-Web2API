"""Upload images through the webpage's file input and verify remote previews."""
from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from pathlib import Path

from .image_input import ImageInput, ImageUploadError

# A local blob preview alone is NOT proof of a completed upload. Require
# a decoded preview served by ChatGPT, exact filenames and an enabled send.
UPLOAD_STATE_JS = """(() => {
  const composer = document.querySelector('#prompt-textarea');
  const form = composer && composer.closest('form');
  if (!form) return JSON.stringify({missing:true});
  const tiles = [...form.querySelectorAll('[role="group"][aria-label]')]
    .filter(e => e.querySelector('img') || [...e.querySelectorAll('button[aria-label]')]
      .some(b => (b.getAttribute('aria-label') || '').includes(e.getAttribute('aria-label'))));
  const names = tiles.map(e => e.getAttribute('aria-label'));
  const ready = __D.names.every(name => {
    const matches = tiles.filter(e => e.getAttribute('aria-label') === name);
    if (matches.length !== 1) return false;
    const tile = matches[0], img = tile.querySelector('img');
    if (!img) return false;
    let remote = false;
    try {
      const url = new URL(img.src);
      remote = url.origin === location.origin && url.pathname.startsWith('/backend-api/');
    } catch (_) {}
    return remote && img.complete && img.naturalWidth > 0 &&
      !tile.querySelector('[role="progressbar"], [aria-busy="true"], [role="alert"]');
  });
  const send = form.querySelector('[data-testid="send-button"]');
  return JSON.stringify({names,ready,sendReady:!!send && !send.disabled && send.getAttribute('aria-disabled') !== 'true'});
})()"""

CLEANUP_JS = """(() => {
  const form = document.querySelector('#prompt-textarea')?.closest('form');
  if (!form) return false;
  for (const tile of form.querySelectorAll('[role="group"][aria-label]')) {
    if (!__D.names.includes(tile.getAttribute('aria-label'))) continue;
    const name = tile.getAttribute('aria-label');
    const buttons = [...tile.querySelectorAll('button[aria-label]')];
    const remove = buttons.find(b => (b.getAttribute('aria-label') || '').includes(name));
    if (remove) remove.click();
  }
  return true;
})()"""


async def clear_pending_images(driver) -> None:
    names = getattr(driver, '_pending_image_names', [])
    if not names:
        return
    await driver._js_with_data_strict(CLEANUP_JS, {"names": names}, timeout=5)
    state = json.loads(await driver._js_with_data_strict(UPLOAD_STATE_JS, {"names": names}, timeout=5))
    if state.get('missing') or any(n in state.get('names', []) for n in names):
        raise ImageUploadError("Unsent image attachments remain; clear them in the browser before retrying")
    driver._pending_image_names = []


async def upload_images(driver, images: list[ImageInput], timeout: float = 90) -> list[str]:
    """Temp files exist in the same host/container as Chrome, then are removed.

    No retry of setFileInputFiles: transport ambiguity must not duplicate an
    upload. A failed upload never reaches click_send.
    """
    driver._assert_owned_tab_required()
    names = [f"w2a-{uuid.uuid4().hex}{img.suffix}" for img in images]
    async with asyncio.timeout(timeout):
        before = json.loads(await driver._js_with_data_strict(UPLOAD_STATE_JS, {"names": []}))
        if before.get('missing') or before.get('names'):
            raise ImageUploadError("Composer is missing or already contains image attachments; clear it before retrying")
        with tempfile.TemporaryDirectory(prefix='w2a-images-') as directory:
            paths = []
            for name, img in zip(names, images, strict=True):
                path = Path(directory) / name
                path.write_bytes(img.data)
                paths.append(str(path.resolve()))
            result = await driver._cdp('Runtime.evaluate', {
                'expression': 'document.querySelector(\'#upload-photos, input[data-testid="upload-photos-input"]\')',
                'returnByValue': False,
            })
            oid = result.get('result', {}).get('result', {}).get('objectId')
            if not oid or result.get('error') or result.get('result', {}).get('exceptionDetails'):
                raise ImageUploadError("Image upload input is unavailable on this webpage")
            driver._pending_image_names = names
            try:
                response = await driver._cdp('DOM.setFileInputFiles', {'objectId': oid, 'files': paths}, _retry=False)
                if response.get('error'):
                    raise ImageUploadError("Browser rejected the image upload")
            finally:
                await driver._cdp('Runtime.releaseObject', {'objectId': oid}, _retry=False)
            stable = 0
            while True:
                state = json.loads(await driver._js_with_data_strict(UPLOAD_STATE_JS, {'names': names}))
                if state.get('missing'):
                    raise ImageUploadError("Composer disappeared while uploading images")
                if any(n not in names for n in state.get('names', [])):
                    raise ImageUploadError("Unexpected image attachment in composer")
                ready = state.get('ready') and state.get('sendReady') and len(state.get('names', [])) == len(names)
                stable = stable + 1 if ready else 0
                if stable >= 2:
                    return names
                await asyncio.sleep(0.5)
