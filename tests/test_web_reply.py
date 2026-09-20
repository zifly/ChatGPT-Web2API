import json
from unittest.mock import AsyncMock

import pytest

from chatgpt_web2api.cdp_driver import CDPDriver
from chatgpt_web2api.web_reply import confirmed_web_reply


def snapshot(**changes):
    return dict({
        "url": "https://chatgpt.com/c/WEB:client-id",
        "user_count": 1, "assistant_count": 1,
        "complete": True, "user_text": "[User]\nReply with exactly: OK", "text": "OK",
    }, **changes)


PROMPT = "[User]\nReply with exactly: OK"


def test_completed_fresh_reply():
    assert confirmed_web_reply(snapshot(), PROMPT) == "OK"


@pytest.mark.parametrize("changes", [
    {"user_text": "some other request"}, {"user_count": 2}, {"assistant_count": 2},
    {"complete": False}, {"text": ""}, {"text": None},
    {"url": "https://other.example/c/WEB:client-id"},
    {"url": "https://chatgpt.com/c/server-id"},
])
def test_rejects_uncorrelated_or_incomplete_reply(changes):
    assert confirmed_web_reply(snapshot(**changes), PROMPT) == ""


@pytest.mark.asyncio
async def test_requires_stable_completed_snapshot(monkeypatch):
    driver = CDPDriver(cdp_port=9222)
    driver._js_strict = AsyncMock(side_effect=[
        json.dumps(snapshot(complete=False)), json.dumps(snapshot()), json.dumps(snapshot()),
    ])
    monkeypatch.setattr("chatgpt_web2api.cdp_driver.asyncio.sleep", AsyncMock())
    assert await driver._read_confirmed_web_reply(PROMPT) == "OK"
    assert driver._js_strict.await_count == 3


@pytest.mark.asyncio
async def test_navigation_does_not_count_as_stable_reply(monkeypatch):
    driver = CDPDriver(cdp_port=9222)
    driver._js_strict = AsyncMock(side_effect=[
        json.dumps(snapshot(url=f"https://chatgpt.com/c/WEB:{i}")) for i in range(4)
    ])
    monkeypatch.setattr("chatgpt_web2api.cdp_driver.asyncio.sleep", AsyncMock())
    assert await driver._read_confirmed_web_reply(PROMPT) == ""


def test_preserves_code_whitespace():
    text = '  {"id":"synthetic:a_b"}\n'
    assert confirmed_web_reply(snapshot(text=text), PROMPT) == text
