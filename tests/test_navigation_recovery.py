"""Navigation faults must not send, reuse stale routes, or trust hidden inputs."""
import asyncio
import json
import shutil
import subprocess
from unittest.mock import AsyncMock

import pytest

from chatgpt_web2api.cdp_driver import (
    NAVIGATION_PROBE_JS,
    CDPDriver,
    NavigationError,
    NavigationReadinessProbe,
    StreamChunk,
)
from tests.test_request_guard import api


def probe(cid='synthetic', *, ready='interactive', usable=True):
    return json.dumps({'url': f'https://chatgpt.com/c/{cid}', 'ready_state': ready,
                       'app_shell': True, 'composer': True, 'composer_usable': usable})


@pytest.mark.asyncio
async def test_reuses_live_ready_conversation_without_navigation():
    driver = CDPDriver(cdp_port=9222)
    driver._current_conv_id = 'synthetic'
    driver._cdp = AsyncMock(return_value={})
    driver._js_strict = AsyncMock(return_value=probe())
    await driver.navigate_conversation('synthetic')
    driver._cdp.assert_not_awaited()
    assert driver._current_conv_id == 'synthetic'
    assert driver._js_strict.await_count >= 3  # route probe + two ready samples


@pytest.mark.asyncio
async def test_stale_cached_identity_navigates_once_to_requested_conversation():
    driver = CDPDriver(cdp_port=9222)
    driver._current_conv_id = 'synthetic'
    driver._cdp = AsyncMock(return_value={})
    driver._js_strict = AsyncMock(side_effect=[probe('different'), probe(), probe()])
    await driver.navigate_conversation('synthetic')
    driver._cdp.assert_awaited_once()
    assert driver._cdp.call_args.args[1]['url'] == 'https://chatgpt.com/c/synthetic'
    assert driver._cdp.call_args.kwargs['_retry'] is False
    assert driver._current_conv_id == 'synthetic'


@pytest.mark.asyncio
async def test_slow_page_can_become_ready_after_old_fifteen_second_budget(monkeypatch):
    driver = CDPDriver(cdp_port=9222)
    driver._cdp = AsyncMock(return_value={})
    clock = [0.0]
    monkeypatch.setattr('chatgpt_web2api.cdp_driver.time.monotonic', lambda: clock[0])

    async def sleep(seconds):
        clock[0] += seconds

    async def read(*args, **kwargs):
        return probe(usable=clock[0] >= 20)

    monkeypatch.setattr('chatgpt_web2api.cdp_driver.asyncio.sleep', sleep)
    driver._js_strict = read
    await driver.navigate_conversation('synthetic')
    assert 20 <= clock[0] < 30
    driver._cdp.assert_awaited_once()


@pytest.mark.asyncio
async def test_hung_navigation_command_has_wall_clock_deadline_and_clears_identity(monkeypatch):
    monkeypatch.setattr('chatgpt_web2api.cdp_driver.NAVIGATION_TIMEOUT_SECONDS', 0.03)
    driver = CDPDriver(cdp_port=9222)
    driver._current_conv_id = 'old'
    reached = []

    async def hung(*args, **kwargs):
        await asyncio.sleep(10)
        reached.append(True)

    driver._cdp = hung
    driver._js_strict = AsyncMock()
    with pytest.raises(NavigationError) as exc:
        await driver.navigate_conversation('synthetic')
    assert exc.value.code == 'navigation_timeout'
    assert driver._current_conv_id is None
    assert reached == []
    driver._js_strict.assert_not_awaited()


@pytest.mark.parametrize('url', [
    'https://chatgpt.com.evil.invalid/c/synthetic',
    'https://evil-chatgpt.com/c/synthetic',
    'http://chatgpt.com/c/synthetic',
    'https://chatgpt.com/c/different',
])
def test_untrusted_or_different_destination_is_never_a_match(url):
    assert not CDPDriver._is_url_at_conversation(url, 'synthetic')


@pytest.mark.parametrize('ready,usable,expected', [
    ('interactive', True, True), ('interactive', False, False),
    ('complete', False, False), ('loading', True, False),
])
def test_document_state_alone_cannot_authorize_send(ready, usable, expected):
    value = NavigationReadinessProbe('https://chatgpt.com/c/synthetic', ready, True, True, usable)
    assert value.is_ready(True) is expected


@pytest.mark.parametrize('kind', ['navigation_timeout', 'navigation_displaced', 'request_timeout'])
@pytest.mark.asyncio
async def test_http_navigation_failures_preserve_not_sent_and_do_not_submit(monkeypatch, kind):
    monkeypatch.setattr('chatgpt_web2api.cdp_driver.NAVIGATION_TIMEOUT_SECONDS',
                        0.035 if kind == 'navigation_timeout' else 30)
    async with api(monkeypatch, budget=0.035 if kind == 'request_timeout' else 2) as (_, d, client, _lock):
        d._is_url_at_conversation = CDPDriver._is_url_at_conversation
        d._target_id = 'test-tab'
        d._ws = object()
        d._breakers = None
        d._assert_owned_tab_required = lambda: None
        d._navigate_conversation_once = CDPDriver._navigate_conversation_once.__get__(d)
        d._cdp = AsyncMock(return_value={})
        d._js_strict = AsyncMock(return_value=probe(usable=False))
        sent = []

        async def send(*args, **kwargs):
            sent.append(True)
            yield StreamChunk('should not happen')

        d.send_and_stream = send
        if kind == 'navigation_displaced':
            d._js_strict.side_effect = [probe(usable=False), probe('other'), probe('other')]

        async def navigate(cid):
            await CDPDriver.navigate_conversation(d, cid)

        d.navigate_conversation = navigate
        response = await client.post('/v1/chat/completions',
            headers={'Authorization': 'Bearer test-only'}, json={
                'model': 'auto', 'stream': False, 'conversation_id': 'synthetic',
                'messages': [{'role': 'user', 'content': 'synthetic test'}]})
        body = await response.json()
        assert response.status == (502 if kind == 'navigation_displaced' else 504)
        error = body['error']
        assert error['code'] == kind
        assert error['phase'] == 'navigation'
        assert error['send_state'] == 'not_sent'
        assert error['prompt_sent'] is False
        assert error['automatic_retry_allowed'] is False
        assert sent == []
        assert d._current_conv_id is None
        if kind != 'request_timeout':
            assert 'navigation' in error
            assert 'synthetic' not in json.dumps(error)


@pytest.mark.parametrize('override,expected', [
    ({}, True), ({'rects': 0}, False), ({'visibility': 'hidden'}, False),
    ({'display': 'none'}, False), ({'disabled': True}, False),
    ({'readOnly': True}, False), ({'ariaDisabled': 'true'}, False),
    ({'inert': True}, False), ({'editable': False}, False),
    ({'editable': False, 'tagName': 'TEXTAREA'}, True),
    ({'count': 0}, False),
])
def test_actual_browser_probe_rejects_unusable_composers(override, expected):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node required to execute the actual browser probe')
    setup = """
const fixture = __FIXTURE__;
const el = {
  disabled: fixture.disabled || false, readOnly: fixture.readOnly || false,
  isContentEditable: fixture.editable !== false, tagName: fixture.tagName || 'DIV',
  getClientRects: () => Array(fixture.rects ?? 1).fill({}),
  getAttribute: () => fixture.ariaDisabled || null,
  closest: () => fixture.inert ? {} : null
};
globalThis.getComputedStyle = () => ({visibility: fixture.visibility || 'visible', display: fixture.display || 'block'});
globalThis.location = {href: 'https://chatgpt.com/c/synthetic'};
globalThis.document = {readyState: 'interactive', querySelector: () => ({}),
  querySelectorAll: () => Array(fixture.count ?? 1).fill(el)};
process.stdout.write(__PROBE__);
""".replace('__FIXTURE__', json.dumps(override)).replace('__PROBE__', NAVIGATION_PROBE_JS)
    result = subprocess.run([node, '-e', setup], text=True, capture_output=True, check=True, timeout=10)
    assert json.loads(result.stdout)['composer_usable'] is expected
