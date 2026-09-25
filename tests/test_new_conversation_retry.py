"""Gateway replacements are explicit new requests, isolated from failed turns."""
import json

import pytest

from chatgpt_web2api.cdp_driver import RateLimitError
from chatgpt_web2api.request_guard import new_conversation_retry_policy
from tests.browser_fixtures import image_part
from tests.test_rest_driver_pool import api, post


async def error_body(response, stream):
    if not stream:
        return await response.json()
    wire = await response.text()
    events = [json.loads(line[6:]) for line in wire.splitlines() if line.startswith('data: {')]
    event = next(e for e in events if 'error' in e)
    assert event['choices'][0]['finish_reason'] == 'error'
    assert not any(e['choices'][0]['finish_reason'] == 'stop' for e in events)
    assert wire.endswith('data: [DONE]\n\n')
    return event


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('unknown', [False, True])
async def test_failed_turn_allows_one_separate_replacement_without_reusing_old_chat(stream, unknown):
    async with api() as (server, drivers, client):
        drivers[0].unknown = unknown
        drivers[0].failure = TimeoutError('synthetic interrupted reply')
        response = await post(client, 'abandoned-chat', stream=stream)
        body = await error_body(response, stream)
        assert body['error']['send_state'] == ('unknown' if unknown else 'confirmed')
        assert body['error']['automatic_retry_allowed'] is False
        assert body['error']['new_conversation_retry'] == {
            'allowed': True, 'mode': 'new_conversation', 'max_retries': 1, 'retry_after_seconds': 2}
        assert sum(len(d.turns) for d in drivers) == 1  # Server never replays.
        content = [{'type': 'text', 'text': 'Rebuilt context and original task'}, image_part()]
        replacement = await post(client, new_conversation=True, content=content)
        result = await replacement.json()
        assert replacement.status == 200
        assert result['conversation_id'] != 'abandoned-chat'
        assert result['request_diagnostics']['request_id'] != body['request_diagnostics']['request_id']
        assert 'Rebuilt context and original task' in result['choices'][0]['message']['content']
        assert len(drivers[1].turns) == 1 and drivers[1].turns[0][2]
        assert server._driver_pool.workers[0].guard.paused
        assert (await post(client, 'abandoned-chat')).status == 503
        assert len(drivers[0].turns) == 1


@pytest.mark.parametrize('stream', [False, True])
async def test_rate_limit_replacement_hint_preserves_account_cooldown_even_in_sse(stream):
    async with api() as (_, drivers, client):
        drivers[0].failure = RateLimitError(retry_after=7)
        body = await error_body(await post(client, 'old', stream=stream), stream)
        policy = body['error']['new_conversation_retry']
        assert policy['allowed'] and policy['retry_after_seconds'] == 7
        # A new ID cannot bypass the shared cooldown.
        blocked = await post(client, new_conversation=True)
        assert blocked.status == 429 and not drivers[1].turns


async def test_validation_and_auth_failures_do_not_advertise_replacement():
    async with api() as (_, drivers, client):
        invalid = await post(client, 'old', new_conversation=True)
        assert invalid.status == 400
        assert (await invalid.json())['error']['new_conversation_retry']['allowed'] is False
        unauthorized = await client.post('/v1/chat/completions', json={})
        assert unauthorized.status == 401
        assert (await unauthorized.json())['error']['new_conversation_retry']['max_retries'] == 0
        assert all(not d.turns for d in drivers)


@pytest.mark.parametrize('status,code,allowed', [
    (500, '', True), (502, 'navigation_failed', True), (503, 'browser_pool_busy', True),
    (504, 'client_disconnected', False), (400, '', False), (401, '', False), (403, '', False),
])
def test_replacement_policy_boundaries(status, code, allowed):
    policy = new_conversation_retry_policy(status, {'code': code})
    assert policy['allowed'] is allowed
    assert policy['max_retries'] == int(allowed)
