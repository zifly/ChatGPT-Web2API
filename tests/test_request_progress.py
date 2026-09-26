"""Live HTTP progress during blocked browser work, without additional sends."""
import asyncio
import gc
import json
import uuid
import weakref
from contextlib import suppress
from types import SimpleNamespace

import pytest

from chatgpt_web2api.cdp_driver import StreamChunk
from chatgpt_web2api.request_guard import (
    BrowserGuard,
    RequestState,
    phase,
    send_attempted,
    send_confirmed,
)
from chatgpt_web2api.request_progress import ProgressCapacityError, RequestProgressStore
from tests.test_health import _health_body
from tests.test_rest_driver_pool import api, post, wait_for

AUTH = {'Authorization': 'Bearer test-only'}


async def progress(client, progress_id, headers=None):
    response = await client.get(f'/v1/requests/{progress_id}', headers=headers or AUTH)
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.status == 200
    return await response.json()


@pytest.mark.parametrize('stream', [False, True])
async def test_progress_visible_before_navigation_finishes_and_through_input_upload_reply(stream):
    async with api() as (server, drivers, client):
        progress_id = str(uuid.uuid4())
        reached = {name: asyncio.Event() for name in ('navigation', 'input', 'upload', 'reply')}
        release = {name: asyncio.Event() for name in reached}

        async def navigate(gizmo_id=None):
            reached['navigation'].set()
            await release['navigation'].wait()
            drivers[0]._current_conv_id = 'synthetic-private-conversation'

        async def send(*args, **kwargs):
            for name in ('input', 'upload'):
                phase(name)
                reached[name].set()
                await release[name].wait()
            send_attempted()
            send_confirmed()
            phase('reply')
            reached['reply'].set()
            await release['reply'].wait()
            yield StreamChunk('synthetic answer')
            yield StreamChunk('', 'stop')

        drivers[0].navigate_new_chat = navigate
        drivers[0].send_and_stream = send
        pending = asyncio.create_task(post(client, new_conversation=True, stream=stream,
                                          progress_id=progress_id, content='synthetic private prompt'))
        observed = []
        try:
            for name in reached:
                await asyncio.wait_for(reached[name].wait(), 1)
                snapshot = await progress(client, progress_id)
                observed.append(snapshot)
                assert snapshot['status'] == 'running' and snapshot['phase'] == name
                assert snapshot['terminal_reason'] is None
                assert snapshot['progress_id'] == progress_id
                assert snapshot['remaining_seconds'] > 0
                assert snapshot['send_state'] == ('confirmed' if name == 'reply' else 'not_sent')
                assert snapshot['phase_timings'][name] >= snapshot['phase_elapsed_seconds']
                wire = json.dumps(snapshot)
                assert all(secret not in wire for secret in (
                    'synthetic private prompt', 'synthetic answer', 'synthetic-private-conversation', 'test-only'))
                assert server._driver_pool.snapshot()['active'] == 1
                if not stream:
                    assert not pending.done()  # Polls succeed while the JSON POST is still waiting.
                release[name].set()
            response = await pending
            if stream:
                wire = await response.text()
                events = [json.loads(line[6:]) for line in wire.splitlines() if line.startswith('data: {')]
                assert events[0]['choices'][0]['delta']['role'] == 'assistant'
                assert wire.endswith('data: [DONE]\n\n')
                result = next(event for event in events if event['choices'][0]['finish_reason'] == 'stop')
                assert all('status' not in event for event in events)
            else:
                result = await response.json()
                assert result['choices'][0]['message']['content'] == 'synthetic answer'
            final = await progress(client, progress_id)
            assert final['status'] == 'succeeded' and final['terminal_reason'] == 'succeeded'
            assert final['request_id'] == result['request_diagnostics']['request_id']
            assert result['request_diagnostics']['progress_id'] == progress_id
            assert {item['request_id'] for item in observed} == {final['request_id']}
            await asyncio.sleep(0.01)
            assert await progress(client, progress_id) == final  # Finished timers freeze.
        finally:
            for event in release.values():
                event.set()
            await asyncio.gather(pending, return_exceptions=True)


async def test_progress_distinguishes_two_running_requests_from_cancelled_queue():
    async with api() as (server, drivers, client):
        ids = [str(uuid.uuid4()) for _ in range(3)]
        for driver in drivers:
            driver.release.clear()
        tasks = []
        try:
            tasks.extend(asyncio.create_task(post(client, cid, progress_id=pid))
                         for cid, pid in zip(('A', 'B'), ids[:2], strict=True))
            await asyncio.wait_for(asyncio.gather(*(d.started.wait() for d in drivers)), 1)
            tasks.append(asyncio.create_task(post(client, 'C', progress_id=ids[2])))
            await wait_for(lambda: server._driver_pool.snapshot()['queued'] == 1)
            snapshots = [await progress(client, pid) for pid in ids]
            assert [s['phase'] for s in snapshots] == ['reply', 'reply', 'queue']
            assert len({s['request_id'] for s in snapshots}) == 3
            tasks[2].cancel()
            with suppress(asyncio.CancelledError):
                await tasks[2]
            await wait_for(lambda: server._driver_pool.snapshot()['queued'] == 0)
            cancelled = await progress(client, ids[2])
            assert cancelled['status'] == 'cancelled'
            # Either aiohttp or the disconnect watcher may cancel first.
            assert cancelled['terminal_reason'] in ('cancelled', 'client_disconnected')
            assert cancelled['send_state'] == 'not_sent'
            assert all(not w.guard.paused for w in server._driver_pool.workers)
        finally:
            for driver in drivers:
                driver.release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert sum(len(d.turns) for d in drivers) == 2  # Cancelled/polled C never sent.


@pytest.mark.parametrize('stream', [False, True])
async def test_deadline_publishes_failed_snapshot_without_replaying(stream):
    async with api(budget=0.15) as (_, drivers, client):
        progress_id = str(uuid.uuid4())
        drivers[0].release.clear()
        response = await post(client, 'old', progress_id=progress_id, stream=stream)
        if stream:
            wire = await response.text()
            assert '"finish_reason": "error"' in wire and '"stop"' not in wire
        else:
            assert response.status == 504
            assert (await response.json())['error']['code'] == 'request_timeout'
        snapshot = await progress(client, progress_id)
        assert snapshot['status'] == 'failed' and snapshot['phase'] == 'reply'
        assert snapshot['terminal_reason'] == 'deadline_exceeded'
        assert snapshot['send_state'] == 'confirmed'
        assert sum(len(d.turns) for d in drivers) == 1


async def test_closing_sse_publishes_cancellation_and_preserves_worker_pause():
    async with api() as (server, drivers, client):
        pid = str(uuid.uuid4())
        drivers[0].release.clear()
        response = await post(client, 'old', progress_id=pid, stream=True)
        await asyncio.wait_for(drivers[0].started.wait(), 1)
        response.close()
        await wait_for(lambda: not server._driver_pool.workers[0].busy)
        snapshot = await progress(client, pid)
        assert snapshot['status'] == 'cancelled'
        assert snapshot['send_state'] == 'confirmed'
        assert server._driver_pool.workers[0].guard.paused
        assert len(drivers[0].turns) == 1


async def test_progress_is_scoped_by_api_key_and_never_grants_authorization():
    async with api() as (server, _, client):
        server._config.server.api_keys.append('another-test-key')
        pid = str(uuid.uuid4())
        await post(client, 'A', progress_id=pid)
        own = await progress(client, pid)
        assert (await client.get(f'/v1/requests/{pid}')).status == 401
        other_headers = {'Authorization': 'Bearer another-test-key'}
        other = await client.get(f'/v1/requests/{pid}', headers=other_headers)
        assert other.status == 404 and (await other.json())['error']['code'] == 'progress_not_found'
        response = await client.post('/v1/chat/completions', headers=other_headers,
                                     json={'progress_id': pid, 'messages': [{'role': 'user', 'content': 'second'}]})
        assert response.status == 200
        assert (await progress(client, pid, other_headers))['request_id'] != own['request_id']
        assert await progress(client, pid) == own


@pytest.mark.parametrize('query_auth', [False, True])
async def test_legacy_query_auth_and_header_auth_share_the_same_key_scope(query_auth):
    async with api() as (_, _, client):
        pid = str(uuid.uuid4())
        response = await client.post('/v1/chat/completions',
            headers={} if query_auth else AUTH,
            params={'key': 'test-only'} if query_auth else {},
            json={'progress_id': pid, 'messages': [{'role': 'user', 'content': 'hello'}]})
        assert response.status == 200
        final = await progress(client, pid)
        query = await client.get(f'/v1/requests/{pid}', params={'key': 'test-only'})
        assert query.status == 200 and await query.json() == final
        # A supplied Bearer key still takes precedence over the query parameter.
        invalid = await client.get(f'/v1/requests/{pid}', params={'key': 'test-only'},
                                   headers={'Authorization': 'Bearer invalid'})
        assert invalid.status == 401


async def test_anonymous_deployment_uses_one_namespace():
    async with api() as (server, _, client):
        server._config.server.api_keys.clear()
        pid = str(uuid.uuid4())
        response = await client.post('/v1/chat/completions',
            json={'progress_id': pid, 'messages': [{'role': 'user', 'content': 'hello'}]})
        assert response.status == 200
        snapshot = await client.get(f'/v1/requests/{pid}')
        assert snapshot.status == 200
        assert (await snapshot.json())['status'] == 'succeeded'


async def test_duplicate_progress_id_cannot_send_or_replace_the_first_record():
    async with api() as (_, drivers, client):
        pid = str(uuid.uuid4())
        drivers[0].release.clear()
        pending = asyncio.create_task(post(client, 'A', progress_id=pid))
        try:
            await asyncio.wait_for(drivers[0].started.wait(), 1)
            original = await progress(client, pid)
            for duplicate in (pid.upper(), pid.replace('-', '')):
                response = await post(client, 'B', progress_id=duplicate)
                assert response.status == 409
                body = await response.json()
                assert body['error']['code'] == 'progress_id_conflict'
                assert body['error']['new_conversation_retry']['allowed'] is False
                assert (await progress(client, pid))['request_id'] == original['request_id']
        finally:
            drivers[0].release.set()
            await pending
        assert (await post(client, 'B', progress_id=pid)).status == 409
        assert sum(len(d.turns) for d in drivers) == 1


@pytest.mark.parametrize('value', [None, 12, {}, '', 'not-a-uuid', 'a' * 50])
async def test_invalid_progress_id_rejected_before_browser_work(value):
    async with api() as (_, drivers, client):
        response = await post(client, progress_id=value)
        assert response.status == 400
        assert (await response.json())['error']['code'] == 'invalid_progress_id'
        assert all(not d.navigations and not d.turns for d in drivers)


async def test_progress_capacity_refuses_new_work_before_browser_mutation():
    async with api() as (server, drivers, client):
        server._progress = RequestProgressStore(capacity=0)
        response = await post(client, progress_id=str(uuid.uuid4()))
        assert response.status == 503
        assert (await response.json())['error']['code'] == 'progress_capacity_exceeded'
        assert all(not d.navigations and not d.turns for d in drivers)


async def test_unknown_progress_and_legacy_calls_do_not_create_records():
    async with api() as (server, _, client):
        health = await _health_body(server)
        assert health['request_progress'] == {
            'supported': True, 'poll_interval_seconds': 1, 'retention_seconds': 300}
        response = await post(client)
        body = await response.json()
        assert response.status == 200 and 'progress_id' not in body['request_diagnostics']
        assert 'phase_timings' in body['request_diagnostics']
        missing = await client.get(f'/v1/requests/{uuid.uuid4()}', headers=AUTH)
        assert missing.status == 404
        invalid = await client.get('/v1/requests/not-a-uuid', headers=AUTH)
        assert invalid.status == 400


async def test_store_is_bounded_expires_terminal_snapshots_and_releases_request_state(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr('chatgpt_web2api.request_progress.time', SimpleNamespace(monotonic=lambda: clock[0]))
    store = RequestProgressStore(capacity=1, retention_seconds=5)
    state = RequestState(asyncio.get_running_loop().time() + 60, BrowserGuard())
    second = RequestState(asyncio.get_running_loop().time() + 60, BrowserGuard())
    store.register('owner', 'first', state)
    with pytest.raises(ProgressCapacityError):
        store.register('owner', 'second', second)
    clock[0] = 20  # Active work must not expire.
    assert store.get('owner', 'first')['status'] == 'running'
    store.finish(state.request_id, 'succeeded')
    reference = weakref.ref(state)
    del state
    gc.collect()
    assert reference() is None
    snapshot = store.get('owner', 'first')
    snapshot['phase_timings']['validation'] = -1
    assert store.get('owner', 'first')['phase_timings']['validation'] >= 0
    clock[0] = 25
    assert store.get('owner', 'first') is None
    store.register('owner', 'second', second)


def test_phase_timings_accumulate_revisited_stages_and_freeze(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr('chatgpt_web2api.request_guard.time', SimpleNamespace(monotonic=lambda: clock[0]))
    state = RequestState(120, BrowserGuard(), started_at=0)
    for when, name in ((1, 'queue'), (4, 'navigation'), (8, 'queue')):
        clock[0] = when
        state.set_phase(name)
    clock[0] = 10
    state.finish()
    clock[0] = 100
    assert state.timing() == {'phase': 'queue', 'elapsed_seconds': 10,
                              'phase_elapsed_seconds': 2,
                              'phase_timings': {'validation': 1, 'queue': 5, 'navigation': 4}}
