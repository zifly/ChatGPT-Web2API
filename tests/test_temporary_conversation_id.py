"""A WEB: client ID is not the persisted conversation's backend ID."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from chatgpt_web2api.backend_client import BackendClient
from chatgpt_web2api.cdp_driver import CDPDriver
from chatgpt_web2api.turn_anchor import TurnReconciliationError, TurnTextResult


@pytest.mark.asyncio
@pytest.mark.parametrize("segment", ["WEB:client-id", "WEB%3Aclient-id"])
async def test_temporary_url_is_not_queryable(segment):
    driver = SimpleNamespace(_js_strict=AsyncMock(return_value=f"https://chatgpt.com/c/{segment}"))
    assert await BackendClient(driver)._conversation_id_from_url() == ""


@pytest.mark.asyncio
async def test_stale_temporary_cached_id_uses_live_url():
    driver = SimpleNamespace(
        _current_conv_id="WEB:client-id",
        _js_strict=AsyncMock(return_value="https://chatgpt.com/g/project/c/server-id?x=1#fragment"),
    )
    assert await BackendClient(driver)._get_live_conversation_id_best_effort() == "server-id"


@pytest.mark.asyncio
async def test_final_reply_waits_for_persisted_id(monkeypatch):
    driver = CDPDriver(cdp_port=9222)
    driver._identity_listener = None
    driver._read_assistant_count_baseline = AsyncMock(return_value=0)
    driver.type_message = AsyncMock()
    driver.click_send = AsyncMock()
    driver._verify_send_acknowledged = AsyncMock(return_value=True)
    driver._js_strict = AsyncMock(side_effect=[
        "https://chatgpt.com/c/WEB:client-id",
        "https://chatgpt.com/c/WEB%3Aclient-id",
        "https://chatgpt.com/c/server-id",
    ])

    async def completed(**kwargs):
        if False:
            yield

    driver._completion = SimpleNamespace(
        stream_until_complete=completed, last_dom_text="", had_non_text_content=False,
    )
    driver._fetch_text_for_turn = AsyncMock(return_value=TurnTextResult(status="matched", text="OK"))
    monkeypatch.setattr("chatgpt_web2api.cdp_driver.asyncio.sleep", AsyncMock())
    chunks = [chunk async for chunk in driver.send_and_stream("Reply with exactly: OK")]
    assert "".join(chunk.delta for chunk in chunks) == "OK"
    assert chunks[-1].finish_reason == "stop"
    assert driver._current_conv_id == "server-id"
    assert driver._fetch_text_for_turn.await_count == 1
    assert driver._fetch_text_for_turn.await_args.args[0] == "server-id"
    driver.click_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_unresolved_id_does_not_return_empty_success(monkeypatch):
    driver = CDPDriver(cdp_port=9222)
    driver._identity_listener = None
    driver._read_assistant_count_baseline = AsyncMock(return_value=0)
    driver.type_message = AsyncMock()
    driver.click_send = AsyncMock()
    driver._verify_send_acknowledged = AsyncMock(return_value=True)
    driver._js_strict = AsyncMock(return_value="https://chatgpt.com/c/WEB:client-id")

    async def completed(**kwargs):
        if False:
            yield

    driver._completion = SimpleNamespace(
        stream_until_complete=completed, last_dom_text="", had_non_text_content=False,
    )
    driver._fetch_text_for_turn = AsyncMock()
    monkeypatch.setattr("chatgpt_web2api.cdp_driver.asyncio.sleep", AsyncMock())
    with pytest.raises(TurnReconciliationError, match="conversation_id_not_ready"):
        _ = [chunk async for chunk in driver.send_and_stream("test")]
    driver._fetch_text_for_turn.assert_not_awaited()
    driver.click_send.assert_awaited_once()
