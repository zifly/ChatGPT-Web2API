import asyncio
import base64
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from chatgpt_web2api.api_server import APIServer
from chatgpt_web2api.config import Config
from chatgpt_web2api.image_input import (
    ImageInputError,
    ImageUploadError,
    decode_image,
    normalize_messages,
)
from chatgpt_web2api.image_upload import clear_pending_images, upload_images
from tests.test_reply_integrity import driver_for


def part(format='PNG', mime='image/png'):
    data = io.BytesIO()
    Image.new('RGB', (8, 8), 'red').save(data, format=format)
    return {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,' + base64.b64encode(data.getvalue()).decode()}}


@pytest.mark.parametrize('format,mime', [('PNG', 'image/png'), ('JPEG', 'image/jpeg'), ('WEBP', 'image/webp')])
def test_real_image_formats(format, mime):
    image = decode_image(part(format, mime))
    assert image.mime == mime
    assert image.data
    assert 'data=' not in repr(image)


@pytest.mark.parametrize('url', ['https://example.com/photo.png', 'http://127.0.0.1/private', 'file:///etc/passwd', 'data:image/png;base64,!!!', 'data:image/png;base64,aGVsbG8='])
def test_invalid_or_remote_image_rejected(url):
    with pytest.raises(ImageInputError):
        decode_image({'image_url': {'url': url}})


def test_mime_mismatch_and_detail_rejected():
    with pytest.raises(ImageInputError, match='MIME'):
        decode_image(part('PNG', 'image/jpeg'))
    p = part()
    p['image_url']['detail'] = 'high'
    with pytest.raises(ImageInputError, match='detail'):
        decode_image(p)


def test_limits_before_decode(monkeypatch):
    p = part()
    monkeypatch.setattr('chatgpt_web2api.image_input.MAX_IMAGE_BYTES', 3)
    with pytest.raises(ImageInputError, match='4 MiB'):
        decode_image(p)


def test_dimensions_and_animation(monkeypatch):
    monkeypatch.setattr('chatgpt_web2api.image_input.MAX_PIXELS', 10)
    with pytest.raises(ImageInputError, match='megapixels'):
        decode_image(part())
    monkeypatch.setattr('chatgpt_web2api.image_input.MAX_PIXELS', 20_000_000)
    data = io.BytesIO()
    Image.new('RGB', (8, 8), 'red').save(data, format='PNG', save_all=True, append_images=[Image.new('RGB', (8, 8), 'blue')], duration=100)
    with pytest.raises(ImageInputError, match='Animated'):
        decode_image({'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(data.getvalue()).decode()}})


def test_normalization_keeps_text_and_images_separate():
    messages, images = normalize_messages([{'role': 'system', 'content': 'policy'}, {'role': 'user', 'content': [{'type': 'text', 'text': 'Look carefully'}, part()]}])
    assert messages[-1]['content'] == 'Look carefully'
    assert len(images) == 1
    assert 'base64' not in json.dumps(messages)


def test_image_only_default_prompt_and_count_limit():
    messages, images = normalize_messages([{'role': 'user', 'content': [part()]}])
    assert messages[0]['content'] == 'Describe the attached image(s).'
    with pytest.raises(ImageInputError, match='At most 4'):
        normalize_messages([{'role': 'user', 'content': [part()] * 5}])


def test_aggregate_limit(monkeypatch):
    p = part()
    monkeypatch.setattr('chatgpt_web2api.image_input.MAX_TOTAL_BYTES', len(decode_image(p).data))
    with pytest.raises(ImageInputError, match='Combined'):
        normalize_messages([{'role': 'user', 'content': [p, p]}])


def test_images_in_history_or_non_user_roles_are_not_silently_discarded():
    for messages in ([{'role': 'user', 'content': [part()]}, {'role': 'user', 'content': 'next'}], [{'role': 'system', 'content': [part()]}]):
        with pytest.raises(ImageInputError, match='last user'):
            normalize_messages(messages)


@pytest.mark.asyncio
async def test_bad_api_image_does_not_touch_browser():
    driver = MagicMock()
    server = APIServer(Config(), driver)
    req = MagicMock()
    req.json = AsyncMock(return_value={'messages': [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'http://localhost/private'}}]}]})
    response = await server._handle_chat(req)
    assert response.status == 400
    assert driver.method_calls == []


@pytest.mark.asyncio
async def test_upload_waits_for_ready_and_removes_temporary_files():
    d = MagicMock()
    saved_paths = []
    states = 0
    async def state(js, data, **kwargs):
        nonlocal states
        if not data['names']:
            return json.dumps({'names': []})
        states += 1
        return json.dumps({'names': data['names'], 'ready': states >= 2, 'sendReady': True})
    async def cdp(method, params, **kwargs):
        if method == 'Runtime.evaluate':
            return {'result': {'result': {'objectId': 'input'}}}
        if method == 'DOM.setFileInputFiles':
            assert kwargs['_retry'] is False
            saved_paths.extend(params['files'])
            assert all(Path(p).read_bytes() == decode_image(part()).data for p in saved_paths)
        return {'result': {}}
    d._js_with_data_strict = AsyncMock(side_effect=state)
    d._cdp = AsyncMock(side_effect=cdp)
    await upload_images(d, [decode_image(part())], timeout=5)
    assert states == 3
    assert saved_paths and all(not Path(p).exists() for p in saved_paths)


@pytest.mark.asyncio
async def test_upload_failure_prevents_send_and_cleans_up(monkeypatch):
    d = driver_for(monkeypatch, [], 'unused')
    cleanup = AsyncMock()
    monkeypatch.setattr('chatgpt_web2api.image_upload.clear_pending_images', cleanup)
    monkeypatch.setattr('chatgpt_web2api.image_upload.upload_images', AsyncMock(side_effect=ImageUploadError('failed')))
    with pytest.raises(ImageUploadError):
        _ = [c async for c in d.send_and_stream('request', images=[decode_image(part())])]
    d.click_send.assert_not_awaited()
    d._fetch_text_for_turn.assert_not_awaited()
    assert cleanup.await_count == 2


@pytest.mark.asyncio
async def test_pending_attachment_blocks_next_text_request(monkeypatch):
    d = driver_for(monkeypatch, [], 'unused')
    monkeypatch.setattr('chatgpt_web2api.image_upload.clear_pending_images', AsyncMock(side_effect=ImageUploadError('uncleared')))
    with pytest.raises(ImageUploadError):
        _ = [c async for c in d.send_and_stream('request')]
    d.type_message.assert_not_awaited()
    d.click_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_image_send_order_and_exact_reply(monkeypatch):
    d = driver_for(monkeypatch, [], 'red square', conv_id='server-id')
    monkeypatch.setenv('W2A_REPLY_SOURCE', 'backend')
    actions = []
    async def typed(text): actions.append('type')
    async def upload(*args, **kwargs): actions.append('upload confirmed')
    async def send(): actions.append('send')
    d.type_message = AsyncMock(side_effect=typed)
    d.click_send = AsyncMock(side_effect=send)
    monkeypatch.setattr('chatgpt_web2api.image_upload.upload_images', upload)
    chunks = [c async for c in d.send_and_stream('request', images=[decode_image(part())])]
    assert actions == ['type', 'upload confirmed', 'send']
    assert ''.join(c.delta for c in chunks) == 'red square'
    assert chunks[-1].finish_reason == 'stop'


@pytest.mark.asyncio
async def test_cleanup_keeps_guard_when_page_missing():
    d = MagicMock()
    d._pending_image_names = ['w2a-image.png']
    d._js_with_data_strict = AsyncMock(side_effect=[True, json.dumps({'missing': True})])
    with pytest.raises(ImageUploadError):
        await clear_pending_images(d)
    assert d._pending_image_names


@pytest.mark.asyncio
async def test_upload_cancellation_removes_temp_files_and_never_sends():
    d = MagicMock()
    d._js_with_data_strict = AsyncMock(return_value=json.dumps({'names': []}))
    files = []
    async def cdp(method, params, **kwargs):
        if method == 'Runtime.evaluate':
            return {'result': {'result': {'objectId': 'input'}}}
        if method == 'DOM.setFileInputFiles':
            files.extend(params['files'])
            raise asyncio.CancelledError()
        return {'result': {}}
    d._cdp = AsyncMock(side_effect=cdp)
    with pytest.raises(asyncio.CancelledError):
        await upload_images(d, [decode_image(part())])
    assert files and all(not Path(p).exists() for p in files)
    assert d._pending_image_names  # next request still has a cleanup guard


@pytest.mark.asyncio
async def test_image_rate_limit_does_not_replay_send():
    from chatgpt_web2api.cdp_driver import RateLimitError
    d = MagicMock()
    calls = 0
    async def send(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise RateLimitError(retry_after=1)
        yield  # async generator contract
    d.send_and_stream = send
    with pytest.raises(RateLimitError):
        await APIServer(Config(), d)._full_response(None, 'auto', 'look', 120, images=[decode_image(part())])
    assert calls == 1


def upload_driver(state_callback):
    d = MagicMock()
    d._js_with_data_strict = AsyncMock(side_effect=state_callback)
    async def cdp(method, params, **kwargs):
        if method == 'Runtime.evaluate':
            return {'result': {'result': {'objectId': 'input'}}}
        return {'result': {}}
    d._cdp = AsyncMock(side_effect=cdp)
    return d


@pytest.mark.asyncio
async def test_upload_timeout_retains_only_safe_state(caplog):
    from chatgpt_web2api.image_input import ImageUploadTimeout
    async def state(js, data, **kwargs):
        return json.dumps({'names': data['names'], 'ready': False, 'sendReady': True,
                           'previews': [{'source': 'blob', 'decoded': True, 'busy': False,
                                         'url': 'https://example.invalid/?private-token=secret'}]})
    d = upload_driver(state)
    with pytest.raises(ImageUploadTimeout) as caught:
        await upload_images(d, [decode_image(part())], timeout=0.03)
    message = str(caught.value)
    assert 'wait_upload_confirmation' in message and 'blob' in message
    assert 'private-token' not in message + caplog.text
    assert not any(name in message + caplog.text for name in d._pending_image_names)
    assert len([c for c in d._cdp.await_args_list if c.args[0] == 'DOM.setFileInputFiles']) == 1
    files = next(c.args[1]['files'] for c in d._cdp.await_args_list if c.args[0] == 'DOM.setFileInputFiles')
    assert all(not Path(p).exists() for p in files)


@pytest.mark.asyncio
async def test_upload_read_timeout_does_not_upload_again(monkeypatch):
    monkeypatch.setattr('chatgpt_web2api.image_upload.asyncio.sleep', AsyncMock())
    reads = 0
    async def state(js, data, **kwargs):
        nonlocal reads
        if not data['names']:
            return json.dumps({'names': []})
        reads += 1
        if reads == 1:
            raise TimeoutError('CDP timeout')
        return json.dumps({'names': data['names'], 'ready': True, 'sendReady': True})
    d = upload_driver(state)
    await upload_images(d, [decode_image(part())], timeout=1)
    assert reads == 3
    assert len([c for c in d._cdp.await_args_list if c.args[0] == 'DOM.setFileInputFiles']) == 1


@pytest.mark.asyncio
async def test_attachment_alert_fails_before_upload_deadline():
    async def state(js, data, **kwargs):
        return json.dumps({'names': data['names'], 'ready': False,
                           'previews': [{'source': 'blob', 'alert': True}] if data['names'] else []})
    with pytest.raises(ImageUploadError, match='webpage reported'):
        await upload_images(upload_driver(state), [decode_image(part())], timeout=1)


def test_timeout_responses_distinguish_sent_and_unsent():
    from chatgpt_web2api.image_input import ImageUploadTimeout
    from chatgpt_web2api.turn_anchor import TurnReconciliationError
    server = APIServer(Config(), MagicMock())
    upload = server._error_response(ImageUploadTimeout('upload timeout'))
    assert upload.status == 504
    error = json.loads(upload.body)['error']
    assert error['code'] == 'image_upload_timeout' and error['prompt_sent'] is False
    reply = server._error_response(TurnReconciliationError(
        'synthetic-id', 'existing_conversation', 'fetch_timeout', {'reason': 'deadline_exceeded'}))
    assert reply.status == 504
    error = json.loads(reply.body)['error']
    assert error['code'] == 'reply_timeout' and error['prompt_sent'] is True
