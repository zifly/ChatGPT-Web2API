"""Shared synthetic browser fixtures for reply, citation and upload regression tests."""
import asyncio
import base64
import io
from unittest.mock import AsyncMock

from PIL import Image

from chatgpt_web2api.cdp_driver import CDPDriver, StreamChunk
from chatgpt_web2api.turn_anchor import TurnAnchor, TurnTextResult


def image_part():
    data = io.BytesIO()
    Image.new('RGB', (2, 2), 'blue').save(data, format='PNG')
    return {'type': 'image_url', 'image_url': {
        'url': 'data:image/png;base64,' + base64.b64encode(data.getvalue()).decode()}}


def driver_for(monkeypatch, deltas, final='', *, mode='fresh_chat',
               status='matched', conv_id='server-id'):
    real_sleep = asyncio.sleep

    async def fast_sleep(delay, result=None):
        # Skip browser polling delays, but still let deadlines/cancellation run.
        await real_sleep(0)
        return result

    monkeypatch.setattr('chatgpt_web2api.protocol_reply.asyncio.sleep', fast_sleep)
    driver = CDPDriver(cdp_port=9222)
    driver._current_conv_id = conv_id if mode == 'existing_conversation' else None
    driver._identity_listener = None
    driver._read_assistant_count_baseline = AsyncMock(return_value=0)
    driver._capture_pre_send_fallback_anchor = AsyncMock(return_value=TurnAnchor(
        sent_text='request', mode=mode,
        conversation_id_at_capture=conv_id if mode == 'existing_conversation' else None))
    driver.type_message = AsyncMock()
    driver.click_send = AsyncMock()
    driver._verify_send_acknowledged = AsyncMock(return_value=True)
    driver._conversation_id_from_url = AsyncMock(return_value=conv_id)
    driver._fetch_text_for_turn = AsyncMock(return_value=TurnTextResult(status, text=final))
    driver._read_confirmed_web_reply = AsyncMock(return_value='')

    async def completion(**kwargs):
        for delta in deltas:
            yield StreamChunk(delta)
        yield StreamChunk('', 'stop')

    driver._completion.stream_until_complete = completion
    monkeypatch.setattr('chatgpt_web2api.image_upload.clear_pending_images', AsyncMock())
    return driver
