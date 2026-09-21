"""Real HTTP/transport fault injection: never send after abandonment or replay."""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from chatgpt_web2api.api_server import APIServer
from chatgpt_web2api.cdp_driver import CDPDriver, StreamChunk
from chatgpt_web2api.cdp_transport import CDPTransport
from chatgpt_web2api.config import Config
from chatgpt_web2api.request_guard import (
    CURRENT_REQUEST,
    BrowserGuard,
    BrowserPausedError,
    RequestState,
    phase,
    send_attempted,
    send_confirmed,
)
from tests.test_health import _health_body


@asynccontextmanager
async def api(monkeypatch, budget=0.08):
    lock = asyncio.Lock()
    @asynccontextmanager
    async def mutation(*args):
        async with lock:
            yield
    monkeypatch.setattr('chatgpt_web2api.api_server.MutationLock', mutation)
    d = AsyncMock(spec=CDPDriver)
    d.port = 9222
    d._current_conv_id = 'synthetic'
    d._js_strict.return_value = '{}'
    async def send(*args, **kwargs):
        send_attempted()
        send_confirmed()
        yield StreamChunk('OK')
        yield StreamChunk('', 'stop')
    d.send_and_stream = send
    config = Config()
    config.server.request_timeout = budget
    config.server.api_keys = ['test-only']
    server = APIServer(config, d)
    async with TestClient(TestServer(server.app)) as client:
        yield server, d, client, lock


async def post(client, stream=False):
    return await client.post('/v1/chat/completions', headers={'Authorization': 'Bearer test-only'},
                             json={'messages': [{'role': 'user', 'content': 'synthetic'}],
                                   'model': 'auto', 'new_conversation': True, 'stream': stream})


@pytest.mark.asyncio
async def test_total_deadline_stops_navigation_before_typing_and_pauses(monkeypatch):
    async with api(monkeypatch) as (s, d, client, _):
        async def slow(**kwargs):
            await asyncio.sleep(0.4)
        d.navigate_new_chat.side_effect = slow
        response = await post(client)
        error = (await response.json())['error']
        assert response.status == 504
        assert (error['code'], error['phase'], error['prompt_sent']) == ('request_timeout', 'navigation', False)
        assert s._browser_guard.paused
        d.type_message.assert_not_called()
        response = await post(client)
        assert response.status == 503
        assert (await response.json())['error']['code'] == 'browser_unresponsive'
        d.navigate_new_chat.assert_awaited_once()
        health = await _health_body(s)
        assert health['status'] == 'degraded' and health['browser_paused']


@pytest.mark.asyncio
async def test_queue_deadline_never_runs_later_or_poisons_active_browser(monkeypatch):
    async with api(monkeypatch) as (s, d, client, lock):
        await lock.acquire()
        try:
            response = await post(client)
            error = (await response.json())['error']
            assert response.status == 504 and error['phase'] == 'queue'
            assert error['send_state'] == 'not_sent'
            assert not s._browser_guard.paused
        finally:
            lock.release()
        await asyncio.sleep(0.02)
        d.navigate_new_chat.assert_not_awaited()
        assert (await post(client)).status == 200


@pytest.mark.asyncio
async def test_real_click_timeout_is_unknown_and_cannot_replay(monkeypatch):
    from chatgpt_web2api.chatgpt_dom import ChatGPTDom
    async with api(monkeypatch, 0.5) as (s, d, client, _):
        d._js.side_effect = ['yes', TimeoutError('CDP timeout: Runtime.evaluate')]
        async def send(*args, **kwargs):
            phase('send_ready')
            await ChatGPTDom(d).click_send()
            yield StreamChunk('should not happen')
        d.send_and_stream = send
        response = await post(client)
        error = (await response.json())['error']
        assert response.status == 504
        assert error['phase'] == 'send' and error['send_state'] == 'unknown'
        assert error['prompt_sent'] is None and not error['automatic_retry_allowed']
        assert s._browser_guard.reason == 'send_outcome_unknown'
        assert d._js.await_count == 2
        assert (await post(client)).status == 503
        assert d._js.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
async def test_reply_deadline_preserves_confirmed_state_in_json_and_sse(monkeypatch, stream):
    async with api(monkeypatch) as (s, d, client, _):
        async def send(*args, **kwargs):
            send_attempted()
            send_confirmed()
            phase('reply')
            await asyncio.sleep(0.4)
            yield StreamChunk('must not escape')
        d.send_and_stream = send
        response = await post(client, stream)
        if stream:
            wire = await response.text()
            events = [json.loads(line[6:]) for line in wire.splitlines()
                      if line.startswith('data: ') and line[6:] != '[DONE]']
            error = events[-1]['error']
            assert events[-1]['choices'][0]['finish_reason'] == 'error'
            assert 'must not escape' not in wire and '"stop"' not in wire
        else:
            assert response.status == 504
            error = (await response.json())['error']
        assert error['prompt_sent'] is True and error['send_state'] == 'confirmed'
        assert error['phase'] == 'reply' and error['code'] == 'request_timeout'


@pytest.mark.asyncio
async def test_disconnect_cancels_before_send(monkeypatch):
    async with api(monkeypatch, 2) as (s, d, client, _):
        cancelled = asyncio.Event()
        async def slow(**kwargs):
            try:
                await asyncio.sleep(1)
            finally:
                cancelled.set()
        d.navigate_new_chat.side_effect = slow
        task = asyncio.create_task(post(client))
        await asyncio.sleep(0.04)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), 0.8)
        await asyncio.sleep(0.01)
        d.type_message.assert_not_called()
        assert s._browser_guard.paused


@pytest.mark.asyncio
async def test_transport_timeouts_pause_and_cancelled_commands_leave_no_pending():
    d = SimpleNamespace(_msg_id=0, _pending={}, _ws=SimpleNamespace(send=AsyncMock()))
    transport = CDPTransport(d)
    guard = BrowserGuard()
    state = RequestState(asyncio.get_running_loop().time() + 2, guard)
    token = CURRENT_REQUEST.set(state)
    try:
        for _ in range(2):
            with pytest.raises(TimeoutError):
                await transport._cdp('Runtime.evaluate', {}, timeout=0.01)
            assert d._pending == {}
        assert guard.paused
        with pytest.raises(BrowserPausedError):
            await transport._cdp('Page.navigate', {})
        assert d._ws.send.await_count == 2
        guard.recover()
        task = asyncio.create_task(transport._cdp('Runtime.evaluate', {}))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert d._pending == {}
    finally:
        CURRENT_REQUEST.reset(token)


@pytest.mark.asyncio
async def test_recovery_is_authenticated_read_only_and_does_not_send(monkeypatch):
    async with api(monkeypatch) as (s, d, client, _):
        s._browser_guard.pause('send_outcome_unknown')
        response = await client.post('/v1/browser/recover')
        assert response.status == 401
        d._js_strict.assert_not_called()
        d._js_strict.return_value = 'w2a-responsive'
        response = await client.post('/v1/browser/recover', headers={'Authorization': 'Bearer test-only'})
        assert response.status == 200 and not s._browser_guard.paused
        d.navigate_new_chat.assert_not_called()
        d.click_send.assert_not_called()
        d.reconnect.assert_not_called()
        d._js_strict.return_value = 'wrong'
        response = await client.post('/v1/browser/recover', headers={'Authorization': 'Bearer test-only'})
        assert response.status == 503 and s._browser_guard.paused


@pytest.mark.asyncio
async def test_recovery_lock_failure_does_not_pause_active_work(monkeypatch):
    async with api(monkeypatch) as (s, d, client, _):
        @asynccontextmanager
        async def busy(*args):
            raise TimeoutError('lock wait expired')
            yield
        monkeypatch.setattr('chatgpt_web2api.api_server.MutationLock', busy)
        response = await client.post('/v1/browser/recover', headers={'Authorization': 'Bearer test-only'})
        assert response.status == 503
        assert (await response.json())['error']['code'] == 'browser_busy'
        assert not s._browser_guard.paused
        d._js_strict.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limit_after_click_never_replays():
    from chatgpt_web2api.cdp_driver import RateLimitError
    from chatgpt_web2api.resilience import retry_on_rate_limit
    state = RequestState(asyncio.get_running_loop().time() + 1, BrowserGuard())
    token = CURRENT_REQUEST.set(state)
    calls = 0
    async def attempt():
        nonlocal calls
        calls += 1
        send_attempted()
        raise RateLimitError('Too many requests', retry_after=1)
    try:
        with pytest.raises(RateLimitError):
            await retry_on_rate_limit(AsyncMock(), attempt)
        assert calls == 1
    finally:
        CURRENT_REQUEST.reset(token)


@pytest.mark.asyncio
async def test_cancelled_image_upload_skips_page_cleanup(monkeypatch):
    from chatgpt_web2api.image_input import decode_image
    from tests.test_image_input import part
    from tests.test_reply_integrity import driver_for
    d = driver_for(monkeypatch, [], 'unused')
    # The helper stubs global asyncio.sleep; use an Event as the blocked upload.
    started = asyncio.Event()
    async def upload(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    cleanup = AsyncMock()
    monkeypatch.setattr('chatgpt_web2api.image_upload.upload_images', upload)
    monkeypatch.setattr('chatgpt_web2api.image_upload.clear_pending_images', cleanup)
    state = RequestState(asyncio.get_running_loop().time() + 1, BrowserGuard())
    token = CURRENT_REQUEST.set(state)
    try:
        async def run():
            return [chunk async for chunk in d.send_and_stream('request', images=[decode_image(part())])]
        task = asyncio.create_task(run())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        cleanup.assert_awaited_once()  # initial cleanup only
        d.click_send.assert_not_awaited()
    finally:
        CURRENT_REQUEST.reset(token)
