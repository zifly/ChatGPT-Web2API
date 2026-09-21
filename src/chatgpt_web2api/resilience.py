"""Legacy non-REST rate-limit handling with an upper waiting budget.

REST propagates rate limits without replaying this factory, which can include
typing and sending. Non-REST callers retain bounded attempts, but a requested
cooldown above the cap is returned to the caller rather than shortened.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .cdp_driver import RATE_LIMIT_DEFAULT_RETRY_AFTER, RateLimitError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Maximum accepted cooldown; longer values terminate instead of retrying early.
_DEFAULT_CAP = 120


async def retry_on_rate_limit(
    driver,
    factory: Callable[[], Awaitable[T]],
    max_attempts: int = 3,
    backoff: float = RATE_LIMIT_DEFAULT_RETRY_AFTER,
    cap: float = _DEFAULT_CAP,
    on_progress: Callable[[str], Awaitable[None]] | None = None,
) -> T:
    """Run ``factory()``; only non-REST callers may retry RateLimitError.

    Args:
        driver: a CDPDriver (used only to call ``dismiss_rate_limit``).
        factory: zero-arg async callable producing the operation to run. Called
            fresh on each attempt so generators/iterators restart cleanly.
        max_attempts: total attempts including the first. ``1`` = no retry.
        backoff: fallback wait (seconds) when the error carries no retry_after.
        cap: maximum accepted cooldown. Longer cooldowns propagate the error
            without a retry. Accepted waits receive small positive jitter.
        on_progress: optional notifier for the backoff pause. When supplied,
            a single "Rate limited, retrying in Ns…" signal is emitted BEFORE
            the sleep so an MCP client's idle timer is reset during what is
            the longest silence in the system (up to ~120s). This is the same
            callback the factory's captured closure uses inside the business
            function — same object, two injection points. Best-effort: a
            failed notification is swallowed (we're already on an error path).

    Returns:
        The result of ``factory()`` on the first non-throttled attempt.

    Raises:
        RateLimitError: if every attempt is throttled (carries the last
            ``retry_after``, so the caller can surface it as a 429).
        Any other exception from ``factory()`` propagates immediately.

    Note on progress across retries: the on_progress counter is bound to the
    outer call_tool invocation and persists across attempts, so the numeric
    progress keeps climbing. But the business function re-streams the response
    from scratch on each retry, so the message text may visually "reset"
    (e.g. "Streaming… 847 chars" → "Assistant is responding…"). Expected —
    see _make_progress_callback in mcp_server.py.
    """
    last_error: RateLimitError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await factory()
        except RateLimitError as e:
            from .request_guard import CURRENT_REQUEST

            state = CURRENT_REQUEST.get()
            # REST permits only explicitly classified read-only preparation
            # recovery. This factory can include typing and sending: never
            # replay it, even when a throttle happened before the send marker.
            if state:
                raise
            last_error = e
            if attempt >= max_attempts:
                logger.warning(
                    "Rate limit persisted after %d attempt(s); giving up.", attempt
                )
                raise
            wait = e.retry_after or backoff
            if not math.isfinite(wait) or wait < 0 or wait > cap:
                # The cap limits how long we can wait, not how early we may
                # retry. Preserve the original rate limit for the caller.
                raise
            # Transient: try to clear the pop-up, then back off and retry.
            try:
                dismissed = await driver.dismiss_rate_limit()
            except Exception:  # best-effort
                dismissed = None  # unknown — see dismiss_rate_limit's tri-state contract
            wait = wait + random.uniform(0, min(wait, 1.0))  # jitter
            logger.info(
                "Rate limit on attempt %d/%d (dismissed=%s); backing off %.1fs",
                attempt, max_attempts, dismissed, wait,
            )
            # Signal BEFORE sleeping — the backoff is the longest silence in
            # the system and the most likely thing to trip a client timeout.
            # Ordering is asserted in tests (test_resilience progress ordering).
            if on_progress is not None:
                try:
                    await on_progress(f"Rate limited, retrying in {wait:.0f}s…")
                except Exception:
                    pass  # don't compound an error-recovery path
            await asyncio.sleep(wait)
    # Unreachable: the loop either returns or re-raises on the last attempt.
    assert last_error is not None
    raise last_error
