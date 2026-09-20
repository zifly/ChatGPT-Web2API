"""Final reply integrity under mutable DOM progress (no network)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from chatgpt_web2api.cdp_driver import CDPDriver, StreamChunk
from chatgpt_web2api.turn_anchor import TurnAnchor, TurnReconciliationError, TurnTextResult


def driver_for(monkeypatch, snapshots, final=None, conv_id="server-id", mode="fresh_chat", status="matched"):
    d = CDPDriver(cdp_port=9222)
    d._identity_listener = None
    d._read_assistant_count_baseline = AsyncMock(return_value=0)
    d._capture_pre_send_fallback_anchor = AsyncMock(return_value=TurnAnchor(sent_text="request", mode=mode))
    d.type_message = AsyncMock()
    d.click_send = AsyncMock()
    d._verify_send_acknowledged = AsyncMock(return_value=True)
    d._conversation_id_from_url = AsyncMock(return_value=conv_id)
    d._fetch_text_for_turn = AsyncMock(return_value=TurnTextResult(status=status, text=final))
    d._read_confirmed_web_reply = AsyncMock(return_value=final or "")
    d._completion = SimpleNamespace(last_dom_text="", had_non_text_content=False)

    async def progress(**kwargs):
        previous = ""
        for current in snapshots:
            if len(current) > len(previous):
                yield StreamChunk(delta=current[len(previous):])
            previous = current
            d._completion.last_dom_text = current
    d._completion.stream_until_complete = progress
    monkeypatch.setattr("chatgpt_web2api.cdp_driver.asyncio.sleep", AsyncMock())
    return d


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshots,final", [
    (["a", "ab", "abc"], "abc"),
    (["abc", "axc"], "axc"),
    (["abc", "axcZ"], "axcZ"),
    (["abcdef", "abc", "abcdZ"], "abcdZ"),
    (["wrong"], "right"),
    (["obsolete long suffix"], "short"),
    (["partial"], '```json\n{"results":[{"candidate_id":"synthetic:a_b","text":"中文\\n\\\""}]}\n```'),
])
async def test_only_verified_final_text_is_emitted(monkeypatch, snapshots, final):
    d = driver_for(monkeypatch, snapshots, final)
    chunks = [c async for c in d.send_and_stream("request")]
    assert "".join(c.delta for c in chunks) == final
    assert chunks[0].delta == final  # even SSE receives no provisional prefix
    assert chunks[-1].finish_reason == "stop"
    anchor = d._fetch_text_for_turn.call_args.args[1]
    assert anchor.sent_text == "request"


@pytest.mark.asyncio
async def test_web_reply_replaces_partial_dom(monkeypatch):
    d = driver_for(monkeypatch, ["broken prefix"], "correct complete text", conv_id="")
    chunks = [c async for c in d.send_and_stream("request")]
    assert "".join(c.delta for c in chunks) == "correct complete text"
    d._read_confirmed_web_reply.assert_awaited_once_with("request")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["matched", "not_ready", "ambiguous", "fetch_failed", "non_text"])
async def test_unverified_or_empty_reply_never_emits_success(monkeypatch, status):
    d = driver_for(monkeypatch, ["partial"], status=status)
    chunks = []
    with pytest.raises(TurnReconciliationError):
        async for c in d.send_and_stream("request"):
            chunks.append(c)
    assert chunks == []


@pytest.mark.asyncio
async def test_missing_id_existing_chat_cannot_use_fresh_fallback(monkeypatch):
    d = driver_for(monkeypatch, ["partial"], "old answer", conv_id="", mode="existing_conversation")
    with pytest.raises(TurnReconciliationError):
        _ = [c async for c in d.send_and_stream("request")]
    d._read_confirmed_web_reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonstream_api_returns_exact_final_body(monkeypatch):
    import json

    from chatgpt_web2api.api_server import APIServer
    from chatgpt_web2api.config import Config
    final = json.dumps({"results": [{"id": "synthetic:a_b", "text": "中文\nquoted: \""}]}, ensure_ascii=False)
    d = driver_for(monkeypatch, ['{"results":', '{"results":{"id":"wrong_'], final)
    server = APIServer(Config(), d)
    response = await server._full_response(None, "auto", "request", 120)
    body = json.loads(response.body)
    assert body["choices"][0]["message"]["content"] == final
    assert body["choices"][0]["finish_reason"] == "stop"
