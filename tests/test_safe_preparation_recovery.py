"""Fault injection across real navigation, HTTP, send checkpoint and CDP wire."""
import asyncio
import json
import shutil
import subprocess
from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest

from chatgpt_web2api.cdp_driver import (
    NAVIGATION_RECOVERY_PROBE_JS,
    CDPDriver,
    NavigationError,
    RateLimitError,
    StreamChunk,
)
from chatgpt_web2api.chatgpt_dom import ChatGPTDom
from chatgpt_web2api.request_guard import (
    CURRENT_REQUEST,
    BrowserGuard,
    RequestState,
    phase,
    send_attempted,
    send_confirmed,
)
from chatgpt_web2api.resilience import retry_on_rate_limit
from tests.test_request_guard import api


@contextmanager
def request_state(budget=120):
    state = RequestState(asyncio.get_running_loop().time() + budget, BrowserGuard(),
                         phase='navigation', preparation_attempt_count=1,
                         allow_preparation_recovery=True)
    token = CURRENT_REQUEST.set(state)
    try:
        yield state
    finally:
        CURRENT_REQUEST.reset(token)


def page(*, ready=False, safe=True, cid='synthetic'):
    return json.dumps({'url': 'https://chatgpt.com/c/' + cid,
                      'ready_state': 'interactive', 'app_shell': True,
                      'composer': True, 'composer_usable': ready, 'recovery_safe': safe})


def setup_navigation(monkeypatch, driver=None):
    monkeypatch.setattr('chatgpt_web2api.cdp_driver.NAVIGATION_TIMEOUT_SECONDS', 0.035)
    monkeypatch.setattr('chatgpt_web2api.cdp_driver.PREPARATION_BACKOFF_SECONDS', (0.001, 0.002))
    d = driver or CDPDriver(cdp_port=9222)
    d._target_id = 'synthetic-tab'
    d._ws = object()
    d._current_conv_id = None
    d._breakers = None
    d._pending_image_names = []
    d._is_url_at_conversation = CDPDriver._is_url_at_conversation
    d._navigate_conversation_once = CDPDriver._navigate_conversation_once.__get__(d)
    d.navigate_conversation = CDPDriver.navigate_conversation.__get__(d)
    d._cdp = AsyncMock(return_value={})
    d._js_strict = AsyncMock(return_value=page())
    return d


@pytest.mark.asyncio
async def test_http_recovery_success_dispatches_actual_send_script_once(monkeypatch):
    # Two stable probes need room within the tiny synthetic navigation budget.
    original_sleep = asyncio.sleep
    async def short_sleep(seconds):
        await original_sleep(min(seconds, 0.002))
    monkeypatch.setattr('chatgpt_web2api.cdp_driver.asyncio.sleep', short_sleep)
    async with api(monkeypatch, budget=120) as (_, d, client, _):
        setup_navigation(monkeypatch, d)
        d._assert_owned_tab_required = lambda: None
        async def read(expr, **kwargs):
            return page(ready=CURRENT_REQUEST.get().preparation_attempt_count > 1)
        d._js_strict = read
        wire = []
        async def js(expr, **kwargs):
            if 'dispatchEvent' in expr:
                wire.append(expr)
                return 'sent'
            return 'yes'
        d._js = js
        async def send(*args, **kwargs):
            phase('send_ready')
            await ChatGPTDom(d).click_send()
            send_confirmed()
            phase('reply')
            yield StreamChunk('complete synthetic answer')
            yield StreamChunk('', 'stop')
        d.send_and_stream = send
        response = await client.post('/v1/chat/completions',
            headers={'Authorization': 'Bearer test-only'}, json={
                'model': 'auto', 'stream': False, 'conversation_id': 'synthetic',
                'messages': [{'role': 'user', 'content': 'synthetic prompt'}]})
        body = await response.json()
        assert response.status == 200
        assert body['conversation_id'] == 'synthetic'
        assert body['choices'][0]['message']['content'] == 'complete synthetic answer'
        diag = body['request_diagnostics']
        assert (diag['preparation_attempt_count'], diag['retry_count']) == (2, 1)
        assert diag['retry_codes'] == ['navigation_timeout']
        assert diag['terminal_reason'] == 'succeeded'
        assert len(wire) == 1
        d._cdp.assert_awaited_once()  # No second Page.navigate.


@pytest.mark.asyncio
@pytest.mark.parametrize('condition', ['draft', 'route', 'target', 'socket', 'unknown',
                                      'transport', 'attachments', 'stream', 'input'])
async def test_unsafe_recovery_never_starts_second_attempt(monkeypatch, condition):
    d = setup_navigation(monkeypatch)
    with request_state() as state:
        async def read(expr, **kwargs):
            if expr == NAVIGATION_RECOVERY_PROBE_JS:
                return page(safe=condition != 'draft', cid='other' if condition == 'route' else 'synthetic')
            if condition == 'target':
                d._target_id = 'changed'
            elif condition == 'socket':
                d._ws = object()
            return page()
        d._js_strict = read
        if condition == 'unknown':
            send_attempted()
            state.phase = 'navigation'  # A later phase change must not reset submission.
        elif condition == 'transport':
            state.transport_uncertain = True
        elif condition == 'attachments':
            d._pending_image_names = ['synthetic.png']
        elif condition == 'stream':
            state.allow_preparation_recovery = False
        elif condition == 'input':
            state.phase = 'input'
        with pytest.raises(NavigationError):
            await d.navigate_conversation('synthetic')
        assert state.retry_codes == []
        assert state.preparation_attempt_count == 1
        d._cdp.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('budget,reason,attempts', [(120, 'attempts_exhausted', 3),
                                                 (10, 'insufficient_budget', 1)])
async def test_attempt_limit_and_reserved_budget_preserve_original_error(monkeypatch, budget, reason, attempts):
    d = setup_navigation(monkeypatch)
    with request_state(budget) as state:
        deadline = state.deadline
        with pytest.raises(NavigationError) as err:
            await d.navigate_conversation('synthetic')
        assert err.value.code == 'navigation_timeout'
        assert state.terminal_reason == reason
        assert state.preparation_attempt_count == attempts
        assert len(state.retry_codes) == attempts - 1
        assert state.deadline == deadline
        assert state.send_attempt_count == 0
        d._cdp.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_during_backoff_never_starts_another_attempt(monkeypatch):
    d = setup_navigation(monkeypatch)
    monkeypatch.setattr('chatgpt_web2api.cdp_driver.PREPARATION_BACKOFF_SECONDS', (5, 15))
    probed = asyncio.Event()
    async def read(expr, **kwargs):
        if expr == NAVIGATION_RECOVERY_PROBE_JS:
            probed.set()
        return page()
    d._js_strict = read
    with request_state() as state:
        task = asyncio.create_task(d.navigate_conversation('synthetic'))
        await asyncio.wait_for(probed.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert state.retry_codes == []
        assert state.send_attempt_count == 0
        d._cdp.assert_awaited_once()


@pytest.mark.asyncio
async def test_rest_rate_limit_never_replays_even_before_send():
    driver = AsyncMock()
    operation = AsyncMock(side_effect=RateLimitError(retry_after=500))
    with request_state():
        with pytest.raises(RateLimitError) as exc:
            await retry_on_rate_limit(driver, operation)
        assert exc.value.retry_after == 500
    operation.assert_awaited_once()
    driver.dismiss_rate_limit.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_rest_retry_after_is_never_shortened_to_cap():
    driver = AsyncMock()
    operation = AsyncMock(side_effect=RateLimitError(retry_after=500))
    with pytest.raises(RateLimitError) as exc:
        await retry_on_rate_limit(driver, operation, cap=10)
    assert exc.value.retry_after == 500
    operation.assert_awaited_once()
    driver.dismiss_rate_limit.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_send_checkpoint_rejected_even_if_state_was_reset():
    with request_state() as state:
        send_attempted()
        state.send_state = 'not_sent'
        with pytest.raises(RuntimeError, match='second submission'):
            send_attempted()
        assert state.send_attempt_count == 1


@pytest.mark.asyncio
async def test_rest_socket_failure_never_reconnects_or_replays():
    from chatgpt_web2api.cdp_transport import CDPTransport
    d = CDPDriver(cdp_port=9222)
    d._ws = AsyncMock()
    d._ws.send.side_effect = OSError('synthetic closed socket')
    d.reconnect = AsyncMock()
    transport = CDPTransport(d)
    transport._should_reconnect = lambda e: True
    with request_state() as state:
        with pytest.raises(OSError):
            await transport._cdp('Input.insertText', {'text': 'synthetic'})
        assert state.transport_uncertain
    d._ws.send.assert_awaited_once()
    d.reconnect.assert_not_awaited()


@pytest.mark.parametrize('change,expected', [({}, True), ({'draft': 'old'}, False),
    ({'form': False}, False), ({'busy': True}, False), ({'attachment': True}, False),
    ({'fileCount': 1}, False), ({'composer': False}, False)])
def test_real_recovery_probe_requires_empty_idle_page(change, expected):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for actual page probe')
    js = """
const f = __FIXTURE__;
const form = {querySelector: () => f.attachment ? {} : null,
 querySelectorAll: () => [{files: Array(f.fileCount || 0).fill({})}]};
const el = {innerText:f.draft || '',isContentEditable:true,tagName:'DIV',
 getClientRects:()=>[{}],getAttribute:()=>null,closest:(s)=>s==='form' && f.form!==false ? form : null};
globalThis.location={href:'https://chatgpt.com/c/synthetic'};
globalThis.getComputedStyle=()=>({visibility:'visible',display:'block'});
globalThis.document={readyState:'interactive',
 querySelectorAll:()=>f.composer===false ? [] : [el],
 querySelector:(s)=>s==='#prompt-textarea' ? (f.composer===false ? null : el) :
 (s.includes('stop-button') ? (f.busy ? {} : null) : {})};
process.stdout.write(__PROBE__);
""".replace('__FIXTURE__', json.dumps(change)).replace('__PROBE__', NAVIGATION_RECOVERY_PROBE_JS)
    result = subprocess.run([node, '-e', js], capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(result.stdout)['recovery_safe'] is expected


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['auth', 'validation', 'success'])
async def test_http_diagnostics_on_early_errors_and_success(monkeypatch, kind):
    async with api(monkeypatch, budget=2) as (_, _, client, _):
        response = await client.post('/v1/chat/completions',
            headers={'Authorization': 'Bearer ' + ('wrong' if kind == 'auth' else 'test-only')},
            json={} if kind == 'validation' else {
                'model': 'auto', 'new_conversation': True,
                'messages': [{'role': 'user', 'content': 'synthetic'}]})
        body = await response.json()
        diag = body['request_diagnostics']
        assert len(diag['request_id']) == 32
        assert diag['retry_count'] == 0
        assert diag['terminal_reason'] == ('succeeded' if kind == 'success' else 'non_recoverable')
        assert diag['preparation_attempt_count'] == (1 if kind == 'success' else 0)


@pytest.mark.asyncio
async def test_hung_probe_cannot_start_recovery_after_local_timeout(monkeypatch):
    from chatgpt_web2api.cdp_transport import CDPTransport
    d = setup_navigation(monkeypatch)
    d._ws = AsyncMock()  # Wire accepts commands but never supplies responses.
    d._js_strict = CDPTransport(d)._js_strict
    nav = d._cdp
    async def command(method, params=None, **kwargs):
        if method == 'Page.navigate':
            return await nav(method, params, **kwargs)
        return await CDPTransport(d)._cdp(method, params, **kwargs)
    d._cdp = command
    with request_state() as state:
        with pytest.raises((NavigationError, TimeoutError)):
            await d.navigate_conversation('synthetic')
        assert state.transport_uncertain
        assert state.retry_codes == []
        assert d._pending == {}
        assert d._ws.send.await_count == 1
        nav.assert_awaited_once()


@pytest.mark.asyncio
async def test_route_changes_during_recovery_preserve_displacement(monkeypatch):
    d = setup_navigation(monkeypatch)
    with request_state() as state:
        async def read(expr, **kwargs):
            return page(cid='other' if state.preparation_attempt_count > 1 else 'synthetic')
        d._js_strict = read
        with pytest.raises(NavigationError) as exc:
            await d.navigate_conversation('synthetic')
        assert exc.value.code == 'navigation_displaced'
        assert state.preparation_attempt_count == 2
        assert state.send_attempt_count == 0
        d._cdp.assert_awaited_once()


@pytest.mark.asyncio
async def test_breaker_tripped_before_recovery_does_not_probe_again(monkeypatch):
    from unittest.mock import Mock
    d = setup_navigation(monkeypatch)
    d._breakers = Mock()
    d._breakers.first_open.return_value = 'synthetic-breaker'
    d._js_strict = AsyncMock(return_value=page())
    with request_state() as state:
        with pytest.raises(NavigationError):
            await d.navigate_conversation('synthetic')
        assert state.retry_codes == []
        assert all(c.args[0] != NAVIGATION_RECOVERY_PROBE_JS for c in d._js_strict.await_args_list)


@pytest.mark.asyncio
async def test_reply_read_recovery_keeps_anchor_and_counts_only_failed_read(monkeypatch):
    from types import SimpleNamespace

    from chatgpt_web2api.protocol_reply import read_protocol_reply
    from chatgpt_web2api.turn_anchor import TurnAnchor, TurnTextResult
    async def no_sleep(_):
        pass
    monkeypatch.setattr('chatgpt_web2api.protocol_reply.asyncio.sleep', no_sleep)
    anchor = TurnAnchor(sent_text='synthetic', mode='existing_conversation',
                        conversation_id_at_capture='synthetic')
    d = SimpleNamespace(_conversation_id_from_url=AsyncMock(return_value='synthetic'),
                        _fetch_text_for_turn=AsyncMock(side_effect=[
                            TurnTextResult('not_ready'), TimeoutError('synthetic read timeout'),
                            TurnTextResult('matched', text='complete answer')]))
    with request_state() as state:
        send_attempted()
        send_confirmed()
        phase('reply')
        result = await read_protocol_reply(d, anchor, 2)
        assert result[1] == 'complete answer'
        assert state.reply_recovery_attempt_count == 1
        assert state.send_attempt_count == 1
        assert all(c.args[1] is anchor for c in d._fetch_text_for_turn.await_args_list)


@pytest.mark.asyncio
async def test_disconnect_during_safety_probe_prevents_retry(monkeypatch):
    d = setup_navigation(monkeypatch)
    monkeypatch.setattr('chatgpt_web2api.cdp_driver.REPLY_RESERVE_SECONDS', 0)
    monkeypatch.setattr('chatgpt_web2api.cdp_driver.PREPARATION_BACKOFF_SECONDS', (0.2, 0.2))
    with request_state(1) as state:
        async def read(expr, **kwargs):
            if expr == NAVIGATION_RECOVERY_PROBE_JS:
                state.disconnected = True
            return page()
        d._js_strict = read
        with pytest.raises(NavigationError):
            await d.navigate_conversation('synthetic')
        assert state.retry_codes == []
        assert state.diagnostics()['terminal_reason'] == 'client_disconnected'
        d._cdp.assert_awaited_once()
