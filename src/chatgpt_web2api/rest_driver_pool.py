"""Bounded REST workers; independent tabs, FIFO per conversation, no send retries.

Scheduling is synchronous on the server's single event loop. A waiting request
owns no browser until its future is granted. Cancellation removes either its
ticket or its granted lease before dispatching again. Paused workers retain
their conversation binding until an explicit recovery probe succeeds.
"""
from __future__ import annotations

import asyncio
import math
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from .breakers import BreakerKind, BreakerRegistry, CircuitOpenError
from .cdp_driver import CDPDriver, RateLimitError
from .config import Config
from .request_guard import BrowserGuard, BrowserPausedError
from .tab_registry import TabRegistry


class RestPoolBusyError(RuntimeError):
    """The queue is full, or the pool is stopping."""


@dataclass(eq=False)
class RestWorker:
    index: int
    driver: CDPDriver
    breakers: BreakerRegistry
    guard: BrowserGuard = field(default_factory=BrowserGuard)
    busy: bool = False
    conversation_id: str | None = None
    # Keys held by this lease, including a new ID published in an SSE stop event.
    held_conversations: set[str] = field(default_factory=set)


@dataclass(eq=False)
class _Ticket:
    conversation_id: str | None
    future: asyncio.Future
    worker: RestWorker | None = None


class RestDriverPool:
    def __init__(self, workers: list[RestWorker], max_queue: int = 32):
        if not workers or max_queue < 0:
            raise ValueError("A REST pool needs workers and a nonnegative queue limit")
        self.workers = workers
        self.max_queue = max_queue
        self._waiting: list[_Ticket] = []
        self._active_conversations: set[str] = set()
        self._closed = False
        self._throttled_until = 0.0

    @classmethod
    async def start(cls, config: Config, primary: CDPDriver) -> RestDriverPool:
        """Warm workers before HTTP starts; background CDP tasks inherit no request."""
        primary_breakers = primary._breakers or BreakerRegistry()
        primary._breakers = primary_breakers
        workers = [RestWorker(0, primary, primary_breakers)]
        pool = cls(workers, config.server.rest_pool_max_queue)
        # Append the suffix AFTER derive_instance_id so W2A_INSTANCE_ID cannot
        # collapse all workers onto the same owned-tab registry entry.
        base = TabRegistry.derive_instance_id(
            cdp_port=config.chrome.cdp_port,
            server_identity=f"rest:{config.server.port}",
        )
        try:
            for index in range(1, config.server.rest_pool_size):
                breakers = BreakerRegistry()
                driver = CDPDriver(
                    cdp_port=config.chrome.cdp_port,
                    tab_mode="owned", parallel_tabs=True,
                    instance_id=f"{base}:rest-worker:{index}", breakers=breakers,
                )
                workers.append(RestWorker(index, driver, breakers))
                await driver.connect()
        except BaseException:
            await pool.close()
            raise
        return pool

    def _dispatch(self):
        for ticket in list(self._waiting):
            error = None
            if self._closed:
                error = RestPoolBusyError("REST browser pool is shutting down")
            elif self._throttled_until > time.monotonic():
                error = RateLimitError(retry_after=math.ceil(self._throttled_until - time.monotonic()))
            else:
                bound = next((w for w in self.workers
                              if ticket.conversation_id and w.conversation_id == ticket.conversation_id
                              and w.guard.paused), None)
                if bound or all(w.guard.paused for w in self.workers):
                    error = BrowserPausedError("Browser worker paused; inspect /health and explicitly recover")
            if error:
                self._waiting.remove(ticket)
                ticket.future.set_exception(error)
                continue
            # Local composer/CDP cooldown must not repeatedly select a broken
            # idle slot while a healthy worker is free. Auth is different: the
            # request path probes recovery, so keep that worker eligible.
            eligible = [w for w in self.workers if not w.guard.paused
                        and w.breakers.first_open() in (None, BreakerKind.AUTH_EXPIRED)]
            if not eligible:
                self._waiting.remove(ticket)
                kind = next(w.breakers.first_open() for w in self.workers if not w.guard.paused)
                ticket.future.set_exception(CircuitOpenError(kind))
                continue
            if ticket.conversation_id in self._active_conversations:
                continue
            available = [w for w in eligible if not w.busy]
            if not available:
                continue
            worker = next((w for w in available if ticket.conversation_id
                           and w.conversation_id == ticket.conversation_id), available[0])
            worker.busy = True
            worker.conversation_id = ticket.conversation_id
            if ticket.conversation_id:
                self.bind_conversation(worker, ticket.conversation_id)
            ticket.worker = worker
            self._waiting.remove(ticket)
            ticket.future.set_result(worker)

    def bind_conversation(self, worker: RestWorker, conversation_id: str):
        worker.conversation_id = conversation_id
        worker.held_conversations.add(conversation_id)
        self._active_conversations.add(conversation_id)

    def _release(self, worker):
        self._active_conversations.difference_update(worker.held_conversations)
        worker.held_conversations.clear()
        worker.busy = False
        self._dispatch()

    @asynccontextmanager
    async def acquire(self, conversation_id: str | None):
        ticket = _Ticket(conversation_id, asyncio.get_running_loop().create_future())
        self._waiting.append(ticket)
        self._dispatch()
        if ticket in self._waiting and len(self._waiting) > self.max_queue:
            self._waiting.remove(ticket)
            ticket.future.set_exception(RestPoolBusyError("REST browser queue is full"))
        try:
            # Shield the future so cancellation after a grant can always
            # release the worker; cancelling the caller never starts later work.
            worker = await asyncio.shield(ticket.future)
            yield worker
        finally:
            if ticket in self._waiting:
                self._waiting.remove(ticket)
                ticket.future.cancel()
            elif ticket.future.done() and not ticket.future.cancelled():
                ticket.future.exception()  # consume a raced exception on cancellation
            if ticket.worker is not None:
                self._release(ticket.worker)

    def throttle(self, seconds: float):
        self._throttled_until = max(self._throttled_until, time.monotonic() + max(0, seconds))
        self._dispatch()

    @asynccontextmanager
    async def recovery(self, index: int):
        worker = self.workers[index]
        if worker.busy or self._closed:
            raise RestPoolBusyError("Browser worker is busy; active work was not interrupted")
        worker.busy = True
        try:
            yield worker
        finally:
            self._release(worker)

    def snapshot(self):
        return {
            "enabled": True, "size": len(self.workers),
            "active": sum(w.busy for w in self.workers),
            "queued": len(self._waiting), "max_queue": self.max_queue,
            "account_retry_after": max(0, round(self._throttled_until - time.monotonic(), 1)),
            "slots": [{
                "slot": w.index, "busy": w.busy,
                "driver_connected": bool(w.driver.is_connected),
                "browser_paused": w.guard.paused, "browser_pause_reason": w.guard.reason,
                "consecutive_cdp_timeouts": w.guard.consecutive_timeouts,
                "breakers": w.breakers.snapshot(),
            } for w in self.workers],
        }

    async def close(self):
        self._closed = True
        self._dispatch()
        # The service owns the primary driver's lifetime. HTTP must already
        # be drained before closing the additional workers.
        await asyncio.gather(*(w.driver.close() for w in self.workers[1:]))
