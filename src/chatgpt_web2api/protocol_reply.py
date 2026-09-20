"""Experimental backend-only reply reader; no DOM text or send replay."""
from __future__ import annotations

import asyncio

from .turn_anchor import TurnReconciliationError


async def read_protocol_reply(driver, anchor, timeout: float) -> tuple[str, str]:
    """Poll the conversation protocol until this anchored turn is complete.

    The browser still performs login and the original send. Only read-only
    backend requests are repeated. Missing IDs never unlock a DOM fallback.
    """
    if timeout <= 0:
        raise ValueError("Reply timeout must be positive")
    conversation_id = ""
    last_status = "conversation_id_not_ready"
    try:
        async with asyncio.timeout(timeout):
            while True:
                current_id = await driver._conversation_id_from_url()
                if current_id:
                    expected_id = conversation_id or anchor.conversation_id_at_capture
                    if expected_id and current_id != expected_id:
                        raise TurnReconciliationError(
                            conversation_id="changed",
                            anchor_mode=anchor.mode,
                            last_status="conversation_changed",
                            diagnostic={},
                        )
                    conversation_id = current_id
                    result = await driver._fetch_text_for_turn(conversation_id, anchor)
                    last_status = result.status
                    if result.status == "matched" and isinstance(result.text, str) and result.text.strip():
                        return conversation_id, result.text
                    if result.status == "auth_failed":
                        from .cdp_driver import AuthExpiredError
                        raise AuthExpiredError("Protocol reply authentication failed")
                await asyncio.sleep(1)
    except TimeoutError as exc:
        raise TurnReconciliationError(
            conversation_id=conversation_id or "unresolved",
            anchor_mode=anchor.mode,
            last_status=last_status,
            diagnostic={"reply_source": "backend", "reason": "deadline_exceeded"},
        ) from exc
