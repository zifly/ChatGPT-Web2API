"""Experimental backend-only reply reader; no DOM text or send replay."""
from __future__ import annotations

import asyncio
import logging

from .turn_anchor import TurnReconciliationError

logger = logging.getLogger(__name__)


def _record_read_recovery():
    from .request_guard import CURRENT_REQUEST

    state = CURRENT_REQUEST.get()
    if state:
        state.check()
        state.reply_recovery_attempt_count += 1


async def read_protocol_reply(driver, anchor, timeout: float) -> tuple[str, str, list[dict]]:
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
                try:
                    current_id = await driver._conversation_id_from_url()
                except TimeoutError:
                    last_status = "conversation_id_read_timeout"
                    logger.warning("Protocol URL read timed out; retrying read within reply deadline")
                    await asyncio.sleep(1)
                    _record_read_recovery()
                    continue
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
                    last_status = "fetch_pending"
                    try:
                        result = await driver._fetch_text_for_turn(conversation_id, anchor)
                    except TimeoutError:
                        # A single CDP read has a shorter deadline than the reply.
                        # Retry only this read, with the original turn anchor. The
                        # outer deadline/cancellation still interrupts the loop.
                        last_status = "fetch_timeout"
                        logger.warning("Protocol result read timed out; retrying read within reply deadline")
                        await asyncio.sleep(1)
                        _record_read_recovery()
                        continue
                    last_status = result.status
                    if result.status == "matched" and isinstance(result.text, str) and result.text.strip():
                        return conversation_id, result.text, result.annotations
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
