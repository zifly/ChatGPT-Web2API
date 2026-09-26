"""Persisted statistics must describe real outcomes without changing chat behavior."""
import asyncio
import gzip
import hashlib
import json
import sqlite3
import threading
import time
from contextlib import suppress
from datetime import datetime
from types import SimpleNamespace

import pytest

from chatgpt_web2api.config import Config
from chatgpt_web2api.usage_stats import BUSINESS_TZ, UsageAttempt, UsageStore
from tests.test_rest_driver_pool import api, post, wait_for

AUTH = {'Authorization': 'Bearer test-only'}
OWNER = hashlib.sha256(b'test-only').hexdigest()


async def report(server, client, period='today', headers=None):
    await server._usage.queue.join()
    response = await client.get('/v1/stats', params={'period': period}, headers=headers or AUTH)
    assert response.status == 200
    assert response.headers['Cache-Control'] == 'no-store'
    return await response.json()


async def test_counts_authenticated_chat_attempts_only_and_exact_json_body_bytes():
    async with api() as (server, _, client):
        raw = json.dumps({'messages': [{'role': 'user', 'content': '你好，统计'}]}, ensure_ascii=False).encode()
        response = await client.post('/chat/completions', headers=AUTH, data=raw)
        assert response.status == 200
        output = await response.read()
        # Health, models, dashboard, failed auth and progress polls are not chats.
        await client.get('/v1/models', headers=AUTH)
        await client.get('/stats')
        await client.get('/stats/app.js')
        await client.get('/v1/requests/not-a-uuid', headers=AUTH)
        denied = await client.post('/v1/chat/completions', json={'messages': []})
        assert denied.status == 401
        data = await report(server, client)
        assert data['summary']['total'] == data['summary']['succeeded'] == 1
        assert data['summary']['request_bytes'] == len(raw)
        assert data['summary']['response_bytes'] == len(output)
        assert data['summary']['success_rate'] == 1
        assert data['recent'][0]['request_id'] == json.loads(output)['request_diagnostics']['request_id']
        assert data['phase_averages']['reply'] is not None
        assert data['live'] == {'active': 0, 'queued': 0}


@pytest.mark.parametrize('stream', [False, True])
async def test_sse_and_json_account_for_actual_wire_body_and_failed_outcomes(stream):
    async with api() as (server, drivers, client):
        response = await post(client, 'A', stream=stream)
        output = await response.read()
        first = await report(server, client)
        assert first['summary']['response_bytes'] == len(output)
        assert first['recent'][0]['stream'] == int(stream)
        drivers[0].failure = RuntimeError('synthetic failure text must not be persisted')
        failure = await post(client, 'A', stream=stream)
        failed_output = await failure.read()
        data = await report(server, client)
        assert data['summary']['total'] == 2
        assert data['summary']['succeeded'] == data['summary']['failed'] == 1
        assert data['summary']['success_rate'] == .5
        assert data['recent'][0]['status'] == 'failed'
        assert data['recent'][0]['http_status'] == (200 if stream else 500)
        assert data['summary']['response_bytes'] == len(output) + len(failed_output)
        assert 'synthetic failure' not in json.dumps(data)


async def test_bad_input_is_a_failed_attempt_and_not_a_browser_send():
    async with api() as (server, drivers, client):
        response = await client.post('/v1/chat/completions', headers=AUTH, data=b'{bad')
        assert response.status == 400
        data = await report(server, client)
        assert data['summary']['failed'] == 1 and data['summary']['succeeded'] == 0
        assert data['recent'][0]['send_state'] == 'not_sent'
        assert data['recent'][0]['request_bytes'] == 4
        assert all(not d.turns for d in drivers)


@pytest.mark.parametrize('transport', ['gzip', 'chunked'])
async def test_input_bytes_are_decoded_body_size_not_headers_or_transport_framing(transport):
    async with api() as (server, _, client):
        raw = json.dumps({'messages': [{'role': 'user', 'content': 'synthetic text ' * 100}]}).encode()
        headers = dict(AUTH)
        async def chunks():
            yield raw[:20]
            yield raw[20:]
        if transport == 'gzip':
            headers['Content-Encoding'] = 'gzip'
            payload = gzip.compress(raw)
        else:
            payload = chunks()
        response = await client.post('/v1/chat/completions', headers=headers, data=payload)
        assert response.status == 200
        data = await report(server, client)
        assert data['summary']['request_bytes'] == len(raw)


async def test_stats_authentication_key_isolation_and_query_compatibility():
    async with api() as (server, _, client):
        server._config.server.api_keys.append('other-key')
        await post(client)
        assert (await client.get('/v1/stats')).status == 401
        assert (await client.get('/v1/stats', headers={'Authorization': 'Bearer wrong'})).status == 401
        own = await report(server, client)
        other = await report(server, client, headers={'Authorization': 'Bearer other-key'})
        assert own['summary']['total'] == 1 and other['summary']['total'] == 0
        assert not other['recent']
        query = await client.get('/v1/stats', params={'key': 'test-only'})
        assert query.status == 200 and (await query.json())['summary']['total'] == 1
        invalid = await client.get('/v1/stats?period=forever', headers=AUTH)
        assert invalid.status == 400


async def test_live_and_cancelled_queue_are_counted_without_sending_later():
    async with api() as (server, drivers, client):
        for driver in drivers:
            driver.release.clear()
        tasks = [asyncio.create_task(post(client, cid)) for cid in ('A', 'B')]
        try:
            await asyncio.wait_for(asyncio.gather(*(d.started.wait() for d in drivers)), 1)
            tasks.append(asyncio.create_task(post(client, 'C')))
            await wait_for(lambda: server._driver_pool.snapshot()['queued'] == 1)
            data = await report(server, client)
            assert data['live'] == {'active': 2, 'queued': 1}
            assert data['summary']['total'] == 0
            tasks[-1].cancel()
            with suppress(asyncio.CancelledError):
                await tasks[-1]
            await wait_for(lambda: server._driver_pool.snapshot()['queued'] == 0)
            data = await report(server, client)
            assert data['summary']['cancelled'] == 1
            assert data['recent'][0]['send_state'] == 'not_sent'
        finally:
            for driver in drivers:
                driver.release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        data = await report(server, client)
        assert data['summary']['total'] == 3 and data['summary']['succeeded'] == 2
        assert sum(len(d.turns) for d in drivers) == 2


async def test_deadline_is_failed_and_disconnected_stream_is_cancelled():
    async with api(budget=.1) as (server, drivers, client):
        drivers[0].release.clear()
        response = await post(client, 'A', stream=True)
        await response.read()
        data = await report(server, client)
        assert data['summary']['failed'] == 1
    async with api() as (server, drivers, client):
        drivers[0].release.clear()
        response = await post(client, 'A', stream=True)
        await asyncio.wait_for(drivers[0].started.wait(), 1)
        response.close()
        await wait_for(lambda: not server._usage.inflight)
        data = await report(server, client)
        assert data['summary']['cancelled'] == 1
        assert data['recent'][0]['response_bytes'] > 0


async def test_dashboard_assets_are_local_and_no_private_files_can_be_fetched():
    async with api() as (_, _, client):
        for path, content_type in (('/stats', 'text/html'), ('/stats/app.js', 'text/javascript'),
                                    ('/stats/style.css', 'text/css')):
            response = await client.get(path)
            assert response.status == 200 and response.content_type == content_type
            assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
            assert response.headers['Cache-Control'] == 'no-store'
            assert 'test-only' not in await response.text()
        for path in ('/stats/usage.sqlite3', '/stats/../usage.sqlite3', '/stats/secrets.env'):
            assert (await client.get(path)).status == 404


async def test_disk_records_survive_restart_and_contain_no_chat_or_key(tmp_path, monkeypatch):
    path = tmp_path / 'private' / 'usage.sqlite3'
    monkeypatch.setenv('W2A_USAGE_DB_PATH', str(path))
    async with api() as (server, _, client):
        await post(client, 'sensitive-conversation', content='private message NEVER STORE THIS')
        assert (await report(server, client))['summary']['total'] == 1
    async with api() as (server, _, client):
        data = await report(server, client)
        assert data['summary']['total'] == 1
    disk = b''.join(file.read_bytes() for file in path.parent.iterdir())
    for secret in (b'NEVER STORE THIS', b'sensitive-conversation', b'test-only'):
        assert secret not in disk


def record_at(owner, when, *, status='succeeded', elapsed=2):
    attempt = UsageAttempt(owner)
    record = attempt.snapshot(10, 200)
    record.update(started_at=when, finished_at=when + elapsed, status=status,
                  elapsed_seconds=elapsed, response_bytes=20, reply_seconds=elapsed)
    return record


async def test_timezone_boundaries_percentile_empty_days_and_recent_limit(tmp_path):
    store = UsageStore(str(tmp_path / 'usage.sqlite3'))
    await store.start()
    try:
        midnight = datetime.now(BUSINESS_TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        # The local date changes at UTC 16:00, not UTC midnight.
        await store._run(store._insert, record_at(OWNER, midnight - 1, status='failed', elapsed=60))
        for i in range(60):
            await store._run(store._insert, record_at(OWNER, midnight + .01, elapsed=i + 1))
        await store._run(store._insert, record_at('other', midnight + .01))
        today = await store.report(OWNER)
        week = await store.report(OWNER, '7d')
        assert today['summary']['total'] == 60
        assert today['summary']['p95_seconds'] == 57
        assert today['summary']['average_seconds'] == 30.5
        assert len(today['recent']) == 50
        assert week['summary']['total'] == 61 and len(week['daily']) == 7
        assert week['daily'][-2]['failed'] == 1 and week['daily'][-1]['succeeded'] == 60
        empty = await store.report('no-records')
        assert empty['summary']['success_rate'] is None and empty['summary']['p95_seconds'] is None
    finally:
        await store.close()


async def test_same_tick_record_is_not_excluded_by_datetime_microsecond_rounding(monkeypatch):
    stamp = 1790436507.7188687
    record = record_at(OWNER, stamp)
    monkeypatch.setattr('chatgpt_web2api.usage_stats.time',
                        SimpleNamespace(time=lambda: stamp, monotonic=time.monotonic))
    store = UsageStore(':memory:')
    await store.start()
    try:
        await store._run(store._insert, record)
        data = await store.report(OWNER)
        assert data['summary']['total'] == 1
        assert data['generated_at'] == stamp
    finally:
        await store.close()


async def test_retention_prunes_old_records_when_reopened(tmp_path):
    path = str(tmp_path / 'usage.sqlite3')
    store = UsageStore(path, retention_days=2)
    await store.start()
    await store._run(store._insert, record_at(OWNER, time.time() - 3 * 86400))
    await store._run(store._insert, record_at(OWNER, time.time() - 3600))
    await store.close()
    store = UsageStore(path, retention_days=2)
    await store.start()
    try:
        data = await store.report(OWNER, '30d')
        assert data['summary']['total'] == 1
        count = await store._run(lambda: store._connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0])
        assert count == 1
    finally:
        await store.close()


async def test_unwritable_database_keeps_chat_available(tmp_path, monkeypatch):
    monkeypatch.setenv('W2A_USAGE_DB_PATH', str(tmp_path))  # A directory is not a database.
    async with api() as (server, _, client):
        response = await post(client)
        assert response.status == 200
        data = await client.get('/v1/stats', headers=AUTH)
        assert data.status == 503
        assert server._usage.health()['dropped_records'] == 1


async def test_write_failure_is_visible_and_does_not_fail_chat(monkeypatch):
    async with api() as (server, _, client):
        def fail(record):
            raise sqlite3.OperationalError('synthetic disk full')
        monkeypatch.setattr(server._usage, '_insert', fail)
        assert (await post(client)).status == 200
        data = await report(server, client)
        assert data['storage']['write_errors'] == 1
        assert data['summary']['total'] == 0


async def test_slow_disk_does_not_hold_up_a_second_chat(monkeypatch):
    async with api() as (server, _, client):
        entered = threading.Event()
        release = threading.Event()
        original = server._usage._insert
        def slow(record):
            entered.set()
            release.wait(3)
            original(record)
        monkeypatch.setattr(server._usage, '_insert', slow)
        try:
            assert (await post(client)).status == 200
            await wait_for(entered.is_set)
            response = await asyncio.wait_for(post(client), .5)
            assert response.status == 200 and not release.is_set()
        finally:
            release.set()
        assert (await report(server, client))['summary']['total'] == 2


async def test_queue_is_bounded_and_reports_dropped_records():
    store = UsageStore(':memory:')
    store.available = True
    store.queue = asyncio.Queue(maxsize=1)
    for _ in range(2):
        attempt = store.begin(OWNER)
        store.finish(attempt, 5, 200)
    assert store.queue.qsize() == 1 and store.dropped_records == 1
    assert not store.inflight


def test_config_usage_environment_and_round_trip(tmp_path, monkeypatch):
    path = str(tmp_path / 'usage.sqlite3')
    monkeypatch.setenv('W2A_USAGE_DB_PATH', path)
    monkeypatch.setenv('W2A_USAGE_RETENTION_DAYS', '14')
    cfg = Config.load(str(tmp_path / 'absent.json'))
    assert cfg.server.usage_db_path == path and cfg.server.usage_retention_days == 14
    saved = cfg.to_dict()
    assert saved['usage_db_path'] == path and saved['usage_retention_days'] == 14
    monkeypatch.setenv('W2A_USAGE_RETENTION_DAYS', '0')
    with pytest.raises(ValueError, match='usage_retention_days'):
        Config.load(str(tmp_path / 'absent.json'))
