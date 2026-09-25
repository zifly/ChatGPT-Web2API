"""Client-controlled conversation selection through both real HTTP formats."""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from chatgpt_web2api.api_server import APIServer
from chatgpt_web2api.cdp_driver import CDPDriver, StreamChunk
from chatgpt_web2api.config import Config
from tests.test_image_input import part


@pytest.fixture
async def api(monkeypatch):
    @asynccontextmanager
    async def lock(*args, **kwargs):
        yield
    monkeypatch.setattr('chatgpt_web2api.api_server.MutationLock', lock)
    driver = AsyncMock(spec=CDPDriver)
    driver._current_conv_id = 'old-conversation'
    driver._js_strict.return_value = '{"text":""}'
    created = 0

    async def navigate_new_chat(**kwargs):
        nonlocal created
        created += 1
        driver._current_conv_id = f'new-conversation-{created}'

    async def navigate_conversation(cid):
        if cid == 'missing-conversation':
            raise RuntimeError('Conversation unavailable')
        driver._current_conv_id = cid

    sends = []

    async def send(text, **kwargs):
        sends.append((driver._current_conv_id, text, kwargs))
        yield StreamChunk(delta='OK')
        yield StreamChunk(delta='', finish_reason='stop')

    driver.navigate_new_chat.side_effect = navigate_new_chat
    driver.navigate_conversation.side_effect = navigate_conversation
    driver.send_and_stream = send
    config = Config()
    config.server.api_keys = ['synthetic-test-key']
    server = APIServer(config, driver)
    server._last_conv_id = 'old-conversation'
    client = TestClient(TestServer(server.app))
    await client.start_server()
    try:
        yield client, driver, sends
    finally:
        await client.close()


async def post(api, **kwargs):
    client, _, _ = api
    return await client.post('/v1/chat/completions', json={
        'model': 'auto', 'messages': [{'role': 'user', 'content': 'Hello'}], **kwargs,
    }, headers={'Authorization': 'Bearer synthetic-test-key'})


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
async def test_force_new_then_continue_then_new_returns_correct_ids(api, stream):
    _, d, sends = api
    first = await post(api, new_conversation=True)
    first_id = (await first.json())['conversation_id']
    assert first.status == 200 and first_id != 'old-conversation'
    continued = await post(api, conversation_id=first_id)
    assert (await continued.json())['conversation_id'] == first_id
    second = await post(api, new_conversation=True, stream=stream)
    assert second.status == 200
    if stream:
        import json
        wire = await second.text()
        events = [json.loads(line[6:]) for line in wire.splitlines() if line.startswith('data: ') and line[6:] != '[DONE]']
        second_id = events[-1]['conversation_id']
        assert events[-1]['choices'][0]['finish_reason'] == 'stop'
        assert wire.rstrip().endswith('data: [DONE]')
    else:
        second_id = (await second.json())['conversation_id']
    assert second_id != first_id
    assert [entry[0] for entry in sends] == [first_id, first_id, second_id]
    assert d.navigate_new_chat.await_count == 2
    d.navigate_conversation.assert_awaited_once_with(first_id)
    d.ensure_current_conversation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('extra', [{}, {'new_conversation': False}, {'conversation_id': None}])
async def test_legacy_implicit_continuation_is_preserved(api, extra):
    _, d, _ = api
    response = await post(api, **extra)
    assert response.status == 200
    assert (await response.json())['conversation_id'] == 'old-conversation'
    d.ensure_current_conversation.assert_awaited_once_with('old-conversation')
    d.navigate_new_chat.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('options', [
    {'new_conversation': True, 'conversation_id': 'existing'},
    {'new_conversation': 'true'}, {'new_conversation': 'false'},
    {'new_conversation': 1}, {'new_conversation': None},
    {'conversation_id': 123}, {'conversation_id': ''}, {'conversation_id': '   '},
])
async def test_invalid_controls_fail_before_browser_mutation(api, options):
    _, d, sends = api
    response = await post(api, **options)
    assert response.status == 400
    assert (await response.json())['error']['type'] == 'invalid_request_error'
    assert d.method_calls == []
    assert sends == []


@pytest.mark.asyncio
async def test_explicit_id_switches_back_after_another_client_created_chat(api):
    _, d, sends = api
    await post(api, new_conversation=True)
    response = await post(api, conversation_id='old-conversation')
    assert (await response.json())['conversation_id'] == 'old-conversation'
    assert sends[-1][0] == 'old-conversation'
    d.navigate_conversation.assert_awaited_once_with('old-conversation')


@pytest.mark.asyncio
async def test_unavailable_explicit_id_never_falls_back_to_a_new_chat(api):
    _, d, sends = api
    response = await post(api, conversation_id='missing-conversation')
    assert response.status == 500
    d.navigate_new_chat.assert_not_awaited()
    assert sends == []


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
async def test_force_new_with_image_preserves_attachment(api, stream):
    _, d, sends = api
    response = await post(api, new_conversation=True, stream=stream, project_id='example-project', messages=[{'role': 'user', 'content': [part()]}])
    await response.read()
    assert response.status == 200
    d.navigate_new_chat.assert_awaited_once_with(gizmo_id='example-project')
    assert len(sends) == 1 and len(sends[0][2]['images']) == 1


@pytest.mark.asyncio
async def test_legacy_system_message_still_creates_new_chat(api):
    _, d, _ = api
    response = await post(api, messages=[{'role': 'system', 'content': 'Independent task'}, {'role': 'user', 'content': 'Hello'}])
    assert response.status == 200
    d.navigate_new_chat.assert_awaited_once()
