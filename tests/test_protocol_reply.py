"""Backend-only mode must not use DOM progress or resend mutations."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from chatgpt_web2api.protocol_reply import read_protocol_reply
from chatgpt_web2api.turn_anchor import TurnAnchor, TurnReconciliationError, TurnTextResult
from tests.test_reply_integrity import driver_for


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fresh_chat", "existing_conversation"])
async def test_backend_mode_skips_dom_and_sends_once(monkeypatch, mode):
    monkeypatch.setenv("W2A_REPLY_SOURCE", "backend")
    final = '  {"array":["中文", "a_b", "quoted: \\\"", "\\n"]}\n'
    d = driver_for(monkeypatch, ["corrupt DOM"], final, mode=mode)
    d._completion.stream_until_complete = AsyncMock(side_effect=AssertionError("DOM must not be polled"))
    chunks = [c async for c in d.send_and_stream("request")]
    assert "".join(c.delta for c in chunks) == final
    assert chunks[-1].finish_reason == "stop"
    d.type_message.assert_awaited_once()
    d.click_send.assert_awaited_once()
    d._read_confirmed_web_reply.assert_not_awaited()
    d._completion.stream_until_complete.assert_not_called()


@pytest.mark.asyncio
async def test_delayed_id_and_incomplete_backend(monkeypatch):
    d = driver_for(monkeypatch, [], "final")
    d._conversation_id_from_url = AsyncMock(side_effect=["", "server-id", "server-id"])
    d._fetch_text_for_turn = AsyncMock(side_effect=[TurnTextResult("not_ready",text="partial"), TurnTextResult("matched",text="complete")])
    anchor = TurnAnchor(sent_text="request", mode="fresh_chat")
    assert await read_protocol_reply(d, anchor, 1) == ("server-id", "complete", [])
    assert d._fetch_text_for_turn.await_count == 2


@pytest.mark.asyncio
async def test_conversation_switch_fails(monkeypatch):
    d = driver_for(monkeypatch, [], status="not_ready")
    d._conversation_id_from_url = AsyncMock(side_effect=["first", "other"])
    with pytest.raises(TurnReconciliationError, match="conversation_changed"):
        await read_protocol_reply(d, TurnAnchor(sent_text="request", mode="fresh_chat"), 1)


@pytest.mark.asyncio
async def test_existing_anchor_rejects_wrong_conversation(monkeypatch):
    d = driver_for(monkeypatch, [], "wrong turn", conv_id="other")
    anchor = TurnAnchor(sent_text="request",mode="existing_conversation",conversation_id_at_capture="original")
    with pytest.raises(TurnReconciliationError, match="conversation_changed"):
        await read_protocol_reply(d, anchor, 1)
    d._fetch_text_for_turn.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["not_ready", "matched", "ambiguous", "non_text", "fetch_failed"])
async def test_deadline_rejects_unverified_result(monkeypatch, status):
    # Do not replace asyncio.sleep: the actual deadline must interrupt polling.
    from types import SimpleNamespace
    d = SimpleNamespace(_conversation_id_from_url=AsyncMock(return_value="server-id"),
                        _fetch_text_for_turn=AsyncMock(return_value=TurnTextResult(status,text="")))
    with pytest.raises(TurnReconciliationError):
        await read_protocol_reply(d, TurnAnchor(sent_text="request",mode="fresh_chat"), 0.01)


@pytest.mark.asyncio
async def test_auth_failure_propagates_without_retry(monkeypatch):
    from chatgpt_web2api.cdp_driver import AuthExpiredError
    d = driver_for(monkeypatch, [], status="auth_failed")
    with pytest.raises(AuthExpiredError):
        await read_protocol_reply(d, TurnAnchor(sent_text="request",mode="fresh_chat"), 1)
    assert d._fetch_text_for_turn.await_count == 1


@pytest.mark.asyncio
async def test_bad_configuration_does_not_send(monkeypatch):
    monkeypatch.setenv("W2A_REPLY_SOURCE", "typo")
    d = driver_for(monkeypatch, [], "answer")
    with pytest.raises(ValueError):
        _ = [c async for c in d.send_and_stream("request")]
    d.type_message.assert_not_awaited()
    d.click_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_closes_capture_scope(monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setenv("W2A_REPLY_SOURCE", "backend")
    d = driver_for(monkeypatch, [], "answer")
    listener = MagicMock()
    listener.reenable_if_stale = AsyncMock()
    listener.wait_for_captured_uuid = AsyncMock(return_value="synthetic-user")
    d._identity_listener = listener
    d._fetch_text_for_turn = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        _ = [c async for c in d.send_and_stream("request")]
    listener.arm_capture_scope.return_value.close.assert_called_once()


@pytest.mark.asyncio
async def test_single_read_timeout_does_not_resend_or_end_reply(monkeypatch):
    monkeypatch.setenv("W2A_REPLY_SOURCE", "backend")
    d = driver_for(monkeypatch, [], "final", mode="existing_conversation")
    d._fetch_text_for_turn = AsyncMock(side_effect=[
        TimeoutError("CDP timeout: Runtime.evaluate"),
        TurnTextResult("matched", text="verified final"),
    ])
    chunks = [c async for c in d.send_and_stream("request")]
    assert "".join(c.delta for c in chunks) == "verified final"
    assert d._fetch_text_for_turn.await_count == 2
    anchors = [call.args[1] for call in d._fetch_text_for_turn.await_args_list]
    assert anchors[0] is anchors[1]
    d.type_message.assert_awaited_once()
    d.click_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_url_timeout_is_read_only_and_next_id_is_checked(monkeypatch):
    d = driver_for(monkeypatch, [], "untrusted")
    d._conversation_id_from_url = AsyncMock(side_effect=[TimeoutError(), "wrong-id"])
    anchor = TurnAnchor(sent_text="request", mode="existing_conversation", conversation_id_at_capture="expected")
    with pytest.raises(TurnReconciliationError, match="conversation_changed"):
        await read_protocol_reply(d, anchor, 1)
    d._fetch_text_for_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_read_timeouts_obey_overall_deadline():
    from types import SimpleNamespace
    d = SimpleNamespace(_conversation_id_from_url=AsyncMock(return_value="server-id"),
                        _fetch_text_for_turn=AsyncMock(side_effect=TimeoutError("CDP timeout")))
    with pytest.raises(TurnReconciliationError) as caught:
        await read_protocol_reply(d, TurnAnchor(sent_text="request", mode="fresh_chat"), 0.02)
    assert caught.value.last_status == "fetch_timeout"
    assert caught.value.diagnostic["reason"] == "deadline_exceeded"


@pytest.mark.asyncio
async def test_overall_deadline_interrupts_inflight_read():
    from types import SimpleNamespace
    async def blocked(*args):
        await asyncio.sleep(60)
    d = SimpleNamespace(_conversation_id_from_url=AsyncMock(return_value="server-id"),
                        _fetch_text_for_turn=AsyncMock(side_effect=blocked))
    with pytest.raises(TurnReconciliationError) as caught:
        await read_protocol_reply(d, TurnAnchor(sent_text="request", mode="fresh_chat"), 0.02)
    assert caught.value.last_status == "fetch_pending"
    assert d._fetch_text_for_turn.await_count == 1
