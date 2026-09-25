import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from chatgpt_web2api.config import Config
from chatgpt_web2api.service import Service


@pytest.mark.asyncio
async def test_desktop_zero_timeout_still_detects_login(monkeypatch):
    monkeypatch.setenv("W2A_LOGIN_TIMEOUT_SECONDS", "0")
    service = Service(Config())
    service._driver = AsyncMock()
    service._driver._js.return_value = "x" * 101
    await asyncio.wait_for(service._wait_for_login(), timeout=1)
    service._driver._js.assert_awaited_once()


@pytest.mark.asyncio
async def test_finite_login_timeout_still_expires(monkeypatch):
    service = Service(Config())
    service._driver = AsyncMock()
    service._driver._js.return_value = ""
    clock = iter([0, 2])
    monkeypatch.setattr("chatgpt_web2api.service.time", SimpleNamespace(monotonic=lambda: next(clock)))
    with pytest.raises(TimeoutError, match="within 1s"):
        await service._wait_for_login(timeout=1)


@pytest.mark.asyncio
async def test_login_wait_honors_shutdown(monkeypatch):
    monkeypatch.setenv("W2A_LOGIN_TIMEOUT_SECONDS", "0")
    service = Service(Config())
    service._driver = AsyncMock()
    service._shutdown_event.set()
    with pytest.raises(asyncio.CancelledError):
        await service._wait_for_login()
    service._driver._js.assert_not_awaited()
