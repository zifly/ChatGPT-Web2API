"""Upload lifecycle regression: exact files, confirmed previews and no replay."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from chatgpt_web2api.image_input import ImageInput, ImageUploadError
from chatgpt_web2api.image_upload import clear_pending_images, upload_images


def browser(*, initial=None, input_present=True, rejected=False):
    paths = []
    calls = []
    states = []

    async def state(expression, data, **kwargs):
        states.append(data)
        if len(states) == 1:
            return json.dumps(initial or {'names': []})
        return json.dumps({'names': data['names'], 'ready': True, 'sendReady': True})

    async def cdp(method, params, **kwargs):
        calls.append(method)
        if method == 'Runtime.evaluate':
            return {'result': {'result': {'objectId': 'file-input'} if input_present else {}}}
        if method == 'DOM.setFileInputFiles':
            assert kwargs['_retry'] is False
            paths.extend(params['files'])
            assert all(Path(p).is_file() for p in paths)
            assert [Path(p).read_bytes() for p in paths] == [b'image-one', b'image-two']
            return {'error': {'message': 'rejected'}} if rejected else {}
        return {}

    return SimpleNamespace(
        _assert_owned_tab_required=lambda: None,
        _js_with_data_strict=AsyncMock(side_effect=state),
        _cdp=AsyncMock(side_effect=cdp), _pending_image_names=[],
    ), paths, calls, states


IMAGES = [ImageInput('image/png', b'image-one'), ImageInput('image/png', b'image-two')]


async def test_upload_keeps_files_until_confirmed_and_removes_them_afterward():
    d, paths, calls, states = browser()
    names = await upload_images(d, IMAGES, timeout=2)
    assert len(set(names)) == 2 and d._pending_image_names == names
    assert len(states) == 3  # empty preflight, then two confirmed previews
    assert calls.count('DOM.setFileInputFiles') == 1
    assert calls.count('Runtime.releaseObject') == 1
    assert all(not Path(p).exists() for p in paths)


@pytest.mark.parametrize('initial', [{'missing': True}, {'names': ['previous.png']}])
async def test_preexisting_attachments_or_missing_composer_never_upload(initial):
    d, paths, calls, _ = browser(initial=initial)
    with pytest.raises(ImageUploadError):
        await upload_images(d, IMAGES)
    assert not calls and not paths


async def test_missing_file_input_fails_without_submission():
    d, paths, calls, _ = browser(input_present=False)
    with pytest.raises(ImageUploadError, match='input is unavailable'):
        await upload_images(d, IMAGES)
    assert calls == ['Runtime.evaluate'] and not paths


async def test_rejected_upload_is_never_replayed_and_releases_object():
    d, paths, calls, _ = browser(rejected=True)
    with pytest.raises(ImageUploadError, match='rejected'):
        await upload_images(d, IMAGES)
    assert calls.count('DOM.setFileInputFiles') == calls.count('Runtime.releaseObject') == 1
    assert all(not Path(p).exists() for p in paths)
    assert len(d._pending_image_names) == 2  # next turn must reconcile cleanup


async def test_cancelled_confirmation_releases_temporary_files():
    d, paths, calls, _ = browser()
    ready = asyncio.Event()
    async def state(expression, data, **kwargs):
        if not data['names']:
            return json.dumps({'names': []})
        ready.set()
        await asyncio.Event().wait()
    d._js_with_data_strict.side_effect = state
    task = asyncio.create_task(upload_images(d, IMAGES))
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls.count('DOM.setFileInputFiles') == 1
    assert all(not Path(p).exists() for p in paths)


@pytest.mark.parametrize('remaining', [[], ['owned.png']])
async def test_cleanup_requires_tracked_attachments_to_disappear(remaining):
    d = SimpleNamespace(_pending_image_names=['owned.png'],
                        _js_with_data_strict=AsyncMock(side_effect=[True, json.dumps({'names': remaining})]))
    if remaining:
        with pytest.raises(ImageUploadError):
            await clear_pending_images(d)
        assert d._pending_image_names == ['owned.png']
    else:
        await clear_pending_images(d)
        assert d._pending_image_names == []
    assert all(call.args[1]['names'] == ['owned.png']
               for call in d._js_with_data_strict.await_args_list)
