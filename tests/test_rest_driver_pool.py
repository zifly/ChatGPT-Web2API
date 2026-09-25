"""REST concurrency through real HTTP, with deterministic synthetic browser turns."""
import asyncio
import base64
import io
import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from chatgpt_web2api.api_server import APIServer
from chatgpt_web2api.breakers import BreakerKind, BreakerRegistry
from chatgpt_web2api.cdp_driver import RateLimitError, StreamChunk
from chatgpt_web2api.config import Config
from chatgpt_web2api.request_guard import CURRENT_REQUEST, phase, send_attempted, send_confirmed
from chatgpt_web2api.rest_driver_pool import RestDriverPool, RestPoolBusyError, RestWorker
from tests.test_health import _health_body


class Browser:
    def __init__(self, index):
        self.index = index
        self.port = 9222
        self.target_id = uuid.uuid4().hex
        self.tab_mode = 'owned'
        self.has_owned_target = self.owns_target = self.is_connected = True
        self._current_conv_id = None
        self._breakers = BreakerRegistry()
        self.turns = []
        self.navigations = []
        self.release = asyncio.Event()
        self.release.set()
        self.started = asyncio.Event()
        self.failure = None
        self.unknown = False
        self.model = 'auto'
        self.project = None
        self.probe = 'w2a-responsive'
        self.close = AsyncMock()

    async def navigate_conversation(self, cid):
        self._current_conv_id = cid
        self.navigations.append(cid)

    async def navigate_new_chat(self, gizmo_id=None):
        self.project = gizmo_id
        await self.navigate_conversation(str(uuid.uuid4()))

    async def select_model(self, model):
        self.model = model
        return True

    async def _js_strict(self, expression, **kwargs):
        return self.probe if expression == "'w2a-responsive'" else '{}'

    async def get_models(self):
        return [{'slug': 'auto'}]

    async def get_projects(self):
        return []

    async def send_and_stream(self, text, **kwargs):
        send_attempted()
        if not self.unknown:
            send_confirmed()
        phase('reply')
        cid = self._current_conv_id
        self.turns.append((cid, text, kwargs.get('images'), CURRENT_REQUEST.get().request_id))
        self.started.set()
        await self.release.wait()
        if self.failure:
            raise self.failure
        assert self._current_conv_id == cid, 'another request navigated this active browser'
        yield StreamChunk(f'{cid}:{text}')
        yield StreamChunk('', 'stop')


@asynccontextmanager
async def api(budget=3, max_queue=32):
    cfg = Config()
    cfg.server.request_timeout = budget
    cfg.server.rest_pool_size = 2
    cfg.chatgpt.parallel_tabs = True
    cfg.server.api_keys = ['test-only']
    drivers = [Browser(0), Browser(1)]
    server = APIServer(cfg, drivers[0])
    server._driver_pool = RestDriverPool([
        RestWorker(i, d, d._breakers) for i, d in enumerate(drivers)
    ], max_queue=max_queue)
    async with TestClient(TestServer(server.app)) as client:
        yield server, drivers, client


def post(client, cid=None, *, stream=False, content='hello', **extra):
    body = {'model': 'auto', 'stream': stream,
            'messages': [{'role': 'user', 'content': content}], **extra}
    if cid is not None:
        body['conversation_id'] = cid
    return client.post('/v1/chat/completions',
                       headers={'Authorization': 'Bearer test-only'}, json=body)


async def wait_for(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


async def test_two_conversations_overlap_and_third_waits():
    async with api() as (server, drivers, client):
        for d in drivers:
            d.release.clear()
        a = asyncio.create_task(post(client, 'A'))
        b = asyncio.create_task(post(client, 'B'))
        await asyncio.wait_for(asyncio.gather(*(d.started.wait() for d in drivers)), 1)
        c = asyncio.create_task(post(client, 'C'))
        await wait_for(lambda: server._driver_pool.snapshot()['queued'] == 1)
        assert server._driver_pool.snapshot()['active'] == 2
        assert sum(len(d.turns) for d in drivers) == 2
        drivers[0].release.set()
        ra, rc = await asyncio.gather(a, c)
        assert (await ra.json())['conversation_id'] == 'A'
        assert (await rc.json())['conversation_id'] == 'C'
        assert not b.done()
        drivers[1].release.set()
        assert (await (await b).json())['conversation_id'] == 'B'
        assert server._driver_pool.snapshot()['active'] == 0


async def test_same_conversation_serializes_without_blocking_other_conversations():
    async with api() as (server, drivers, client):
        drivers[0].release.clear()
        a1 = asyncio.create_task(post(client, 'A', content='first'))
        await drivers[0].started.wait()
        a2 = asyncio.create_task(post(client, 'A', content='second'))
        await wait_for(lambda: server._driver_pool.snapshot()['queued'] == 1)
        b = await post(client, 'B')
        assert b.status == 200 and not a2.done()
        assert [t[0] for d in drivers for t in d.turns].count('A') == 1
        drivers[0].release.set()
        await asyncio.gather(a1, a2)
        a_turns = [t[1] for d in drivers for t in d.turns if t[0] == 'A']
        assert a_turns == ['[User]\nfirst', '[User]\nsecond']


async def test_pool_omitted_id_always_starts_fresh_and_explicit_id_resumes():
    async with api() as (_, drivers, client):
        first = await (await post(client, new_conversation=True)).json()
        second = await (await post(client)).json()
        assert first['conversation_id'] != second['conversation_id']
        resumed = await (await post(client, first['conversation_id'])).json()
        assert resumed['conversation_id'] == first['conversation_id']
        assert len(drivers[0].turns) == 3


async def test_queue_timeout_cannot_send_later_or_pause_an_active_worker():
    async with api(budget=0.12) as (server, drivers, client):
        # External leases occupy both slots without sharing the request deadline.
        async with server._driver_pool.acquire('A'), server._driver_pool.acquire('B'):
            response = await post(client, 'C')
            error = (await response.json())['error']
            assert response.status == 504
            assert (error['phase'], error['send_state']) == ('queue', 'not_sent')
            assert server._driver_pool.snapshot()['queued'] == 0
            assert all(not w.guard.paused for w in server._driver_pool.workers)
        await asyncio.sleep(0.02)
        assert all(not d.turns for d in drivers)
        assert (await post(client, 'D')).status == 200


async def test_queue_overflow_is_bounded_and_no_send():
    async with api(max_queue=1) as (server, drivers, client):
        async with server._driver_pool.acquire('A'), server._driver_pool.acquire('B'):
            waiting = asyncio.create_task(post(client, 'C'))
            await wait_for(lambda: server._driver_pool.snapshot()['queued'] == 1)
            response = await post(client, 'D')
            error = (await response.json())['error']
            assert response.status == 503 and error['code'] == 'browser_pool_busy'
            assert error['prompt_sent'] is False
        assert (await waiting).status == 200
        assert all(t[0] != 'D' for d in drivers for t in d.turns)


@pytest.mark.parametrize('stream', [False, True])
async def test_failed_worker_isolated_and_same_id_cannot_bypass_pause(stream):
    async with api() as (server, drivers, client):
        drivers[0].unknown = True
        drivers[0].failure = TimeoutError('synthetic uncertain send')
        response = await post(client, 'A', stream=stream)
        if stream:
            events = [json.loads(line[6:]) for line in (await response.text()).splitlines()
                      if line.startswith('data: {')]
            error_event = next(e for e in events if 'error' in e)
            assert error_event['choices'][0]['finish_reason'] == 'error'
            assert not any(e.get('choices', [{}])[0].get('finish_reason') == 'stop' for e in events)
        else:
            error_event = await response.json()
            assert response.status == 504
        assert error_event['error']['send_state'] == 'unknown'
        assert error_event['request_diagnostics']['browser_slot'] == 0
        assert (await post(client, 'B')).status == 200
        assert (await post(client, 'A')).status == 503
        assert len(drivers[0].turns) == 1
        health = await _health_body(server)
        assert health['status'] == 'degraded' and not health['browser_paused']
        assert health['rest_pool']['slots'][0]['browser_paused']
        response = await client.post('/v1/browser/recover?slot=0',
                                     headers={'Authorization': 'Bearer test-only'})
        assert response.status == 200 and not server._driver_pool.workers[0].guard.paused
        assert len(drivers[0].turns) == 1  # recovery never replays
        drivers[0].unknown, drivers[0].failure = False, None
        assert (await post(client, 'A')).status == 200


async def test_failed_new_chat_keeps_its_discovered_id_quarantined():
    async with api() as (_, drivers, client):
        drivers[0].failure = TimeoutError('reply unknown')
        assert (await post(client, new_conversation=True)).status == 504
        cid = drivers[0]._current_conv_id
        assert cid and (await post(client, cid)).status == 503
        assert (await post(client, 'unrelated')).status == 200


async def test_account_rate_limit_rejects_other_workers_and_waiters():
    async with api() as (server, drivers, client):
        drivers[0].failure = RateLimitError(retry_after=10)
        assert (await post(client, 'A')).status == 429
        response = await post(client, 'B')
        error = (await response.json())['error']
        assert response.status == 429 and int(response.headers['Retry-After']) > 0
        assert error['send_state'] == 'not_sent'
        assert not drivers[1].turns
        assert server._driver_pool.snapshot()['account_retry_after'] > 0


async def test_sse_image_turn_and_text_turn_use_independent_drivers():
    picture = io.BytesIO()
    Image.new('RGB', (2, 2), 'blue').save(picture, format='PNG')
    content = [{'type': 'text', 'text': 'image-A'}, {'type': 'image_url', 'image_url': {
        'url': 'data:image/png;base64,' + base64.b64encode(picture.getvalue()).decode()}}]
    async with api() as (_, drivers, client):
        drivers[0].release.clear()
        a = asyncio.create_task(post(client, 'A', stream=True, content=content))
        await drivers[0].started.wait()
        b = await (await post(client, 'B', content='text-B')).json()
        assert b['conversation_id'] == 'B'
        assert 'image-A' not in b['choices'][0]['message']['content']
        assert drivers[0].turns[0][2] and drivers[1].turns[0][2] is None
        assert drivers[0].turns[0][3] != drivers[1].turns[0][3]
        drivers[0].release.set()
        wire = await (await a).text()
        events = [json.loads(line[6:]) for line in wire.splitlines() if line.startswith('data: {')]
        stop = next(e for e in events if e['choices'][0]['finish_reason'] == 'stop')
        assert stop['conversation_id'] == 'A' and wire.endswith('data: [DONE]\n\n')


async def test_disconnect_while_queued_removes_ticket():
    async with api() as (server, drivers, client):
        async with server._driver_pool.acquire('A'), server._driver_pool.acquire('B'):
            task = asyncio.create_task(post(client, 'C'))
            await wait_for(lambda: server._driver_pool.snapshot()['queued'] == 1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await wait_for(lambda: server._driver_pool.snapshot()['queued'] == 0)
        assert all(not d.turns for d in drivers)


async def test_cancel_after_grant_returns_slot():
    d = Browser(0)
    pool = RestDriverPool([RestWorker(0, d, d._breakers)])
    async def waiting():
        async with pool.acquire('B'):
            pytest.fail('Cancelled waiter must not start')
    async with pool.acquire('A'):
        task = asyncio.create_task(waiting())
        await wait_for(lambda: pool.snapshot()['queued'] == 1)
    # Grant has happened, but the waiter has not resumed yet.
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert pool.snapshot()['active'] == pool.snapshot()['queued'] == 0
    async with pool.acquire('B'):
        pass


async def test_recovery_cannot_interrupt_busy_worker_and_bad_slot_is_400():
    async with api() as (server, drivers, client):
        headers = {'Authorization': 'Bearer test-only'}
        async with server._driver_pool.acquire('A'):
            response = await client.post('/v1/browser/recover?slot=0', headers=headers)
            assert response.status == 503
        for slot in ('-1', '2', 'oops'):
            response = await client.post('/v1/browser/recover?slot=' + slot, headers=headers)
            assert response.status == 400
        assert (await client.post('/v1/browser/recover?slot=0')).status == 401
        assert not drivers[0].turns


async def test_global_chrome_breaker_still_blocks_all_workers():
    async with api() as (server, drivers, client):
        server._breakers.trip(BreakerKind.CHROME_CRASH_LOOP, 'synthetic')
        assert (await post(client, 'A')).status == 503
        assert all(not d.turns for d in drivers)


async def test_local_circuit_does_not_starve_a_healthy_worker():
    async with api() as (_, drivers, client):
        drivers[0]._breakers.trip(BreakerKind.COMPOSER_SEND_READINESS, 'synthetic')
        assert (await post(client, 'A')).status == 200
        assert not drivers[0].turns and len(drivers[1].turns) == 1
        drivers[1]._breakers.trip(BreakerKind.CDP_RECONNECT, 'synthetic')
        response = await post(client, 'B')
        assert response.status == 503 and (await response.json())['error']['code'] == 'circuit_open'


async def test_active_disconnect_pauses_only_its_worker():
    async with api() as (server, drivers, client):
        drivers[0].release.clear()
        task = asyncio.create_task(post(client, 'A'))
        await drivers[0].started.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await wait_for(lambda: server._driver_pool.workers[0].guard.paused
                       and server._driver_pool.snapshot()['active'] == 0)
        assert server._driver_pool.snapshot()['active'] == 0
        assert (await post(client, 'B')).status == 200
        assert (await post(client, 'A')).status == 503
        assert len(drivers[0].turns) == 1


async def test_pool_start_uses_unique_ids_even_with_instance_override(monkeypatch):
    cfg = Config()
    cfg.server.rest_pool_size = 3
    monkeypatch.setenv('W2A_INSTANCE_ID', 'explicit-base')
    created = []
    def factory(**kwargs):
        d = Browser(len(created) + 1)
        d.connect = AsyncMock()
        d.kwargs = kwargs
        created.append(d)
        return d
    monkeypatch.setattr('chatgpt_web2api.rest_driver_pool.CDPDriver', factory)
    primary = Browser(0)
    pool = await RestDriverPool.start(cfg, primary)
    assert {d.kwargs['instance_id'] for d in created} == {
        'explicit-base:rest-worker:1', 'explicit-base:rest-worker:2'}
    await pool.close()
    assert all(d.close.await_count == 1 for d in created)
    primary.close.assert_not_awaited()
    with pytest.raises(RestPoolBusyError):
        async with pool.acquire('A'):
            pass


async def test_partial_start_failure_closes_extra_tabs(monkeypatch):
    cfg = Config()
    cfg.server.rest_pool_size = 2
    d = Browser(1)
    d.connect = AsyncMock(side_effect=RuntimeError('synthetic connect failure'))
    monkeypatch.setattr('chatgpt_web2api.rest_driver_pool.CDPDriver', lambda **kwargs: d)
    with pytest.raises(RuntimeError, match='synthetic'):
        await RestDriverPool.start(cfg, Browser(0))
    d.close.assert_awaited_once()


def test_config_pool_roundtrip_and_environment(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'rest_pool_size': 2, 'rest_pool_max_queue': 4,
                                'parallel_tabs': True, 'tab_mode': 'owned'}))
    cfg = Config.load(str(path))
    assert cfg.to_dict()['rest_pool_size'] == 2
    monkeypatch.setenv('W2A_REST_POOL_SIZE', '3')
    monkeypatch.setenv('W2A_REST_POOL_MAX_QUEUE', '8')
    cfg = Config.load(str(path))
    assert cfg.server.rest_pool_size == 3 and cfg.server.rest_pool_max_queue == 8


@pytest.mark.parametrize('overrides', [
    {'rest_pool_size': 0}, {'rest_pool_max_queue': -1},
    {'rest_pool_size': 2},
    {'rest_pool_size': 2, 'parallel_tabs': True, 'tab_mode': 'adopt'},
])
def test_config_rejects_unsafe_pool(tmp_path, overrides):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(overrides))
    with pytest.raises(ValueError):
        Config.load(str(path))
