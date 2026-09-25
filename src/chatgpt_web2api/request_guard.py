"""REST request deadlines and conservative send-state tracking.

Context-local state prevents queued callers from overwriting an active turn.
No prompt, token, image or conversation data is stored here.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field


class BrowserPausedError(RuntimeError):
    pass


@dataclass
class BrowserGuard:
    consecutive_timeouts: int = 0
    paused: bool = False
    reason: str | None = None

    def check(self):
        if self.paused:
            raise BrowserPausedError('Browser operations paused; check the browser and explicitly recover')

    def pause(self, reason):
        self.paused = True
        self.reason = reason

    def timed_out(self):
        self.consecutive_timeouts += 1
        if self.consecutive_timeouts >= 2:
            self.pause('consecutive_cdp_timeouts')

    def responded(self):
        if not self.paused:
            self.consecutive_timeouts = 0

    def recover(self):
        self.consecutive_timeouts = 0
        self.paused = False
        self.reason = None


@dataclass
class RequestState:
    deadline: float
    guard: BrowserGuard
    phase: str = 'validation'
    send_state: str = 'not_sent'
    disconnected: bool = False
    sse_response: object = None
    failed: bool = False
    deadline_expired: bool = False
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: float = field(default_factory=time.monotonic)
    preparation_attempt_count: int = 0
    retry_codes: list[str] = field(default_factory=list)
    reply_recovery_attempt_count: int = 0
    terminal_reason: str | None = None
    send_attempt_count: int = 0
    transport_uncertain: bool = False
    allow_preparation_recovery: bool = False
    worker: object = None

    def diagnostics(self, *, succeeded=False):
        reason = self.terminal_reason or 'non_recoverable'
        if succeeded:
            reason = 'succeeded'
        elif self.disconnected:
            reason = 'client_disconnected'
        elif self.send_state == 'unknown':
            reason = 'send_outcome_unknown'
        elif self.deadline_expired or self.remaining() <= 0:
            reason = 'deadline_exceeded'
        return {'request_id': self.request_id,
                **({'browser_slot': self.worker.index} if self.worker is not None else {}),
                'preparation_attempt_count': self.preparation_attempt_count,
                'retry_count': len(self.retry_codes), 'retry_codes': self.retry_codes[:2],
                'reply_recovery_attempt_count': self.reply_recovery_attempt_count,
                'elapsed_seconds': round(time.monotonic() - self.started_at, 3),
                'terminal_reason': reason}

    def remaining(self):
        if self.deadline_expired:
            return 0
        return max(0, self.deadline - asyncio.get_running_loop().time())

    def check(self):
        if self.disconnected or self.remaining() <= 0:
            raise TimeoutError('Request deadline or client disconnect')
        self.guard.check()

    def fields(self):
        return {'phase': self.phase, 'send_state': self.send_state,
                'prompt_sent': {'not_sent': False, 'confirmed': True, 'unknown': None}[self.send_state],
                # Legacy clients must not blindly replay the original request.
                # A gateway may instead apply the separate replacement policy.
                'automatic_retry_allowed': False}


def new_conversation_retry_policy(status: int, error: dict, retry_after: str = '') -> dict:
    """Advertise one gateway-owned replacement; never replay inside the driver.

    The gateway owns the logical task budget and must discard the old attempt,
    rebuild its inputs and explicitly start a new conversation. The HTTP server
    cannot count retries across independent client requests.
    """
    allowed = (status == 429 or 500 <= status <= 599) and error.get('code') != 'client_disconnected'
    delay = max(2, int(retry_after or 0)) if allowed else 0
    return {'allowed': allowed, 'mode': 'new_conversation',
            'max_retries': 1 if allowed else 0, 'retry_after_seconds': delay}


CURRENT_REQUEST: ContextVar[RequestState | None] = ContextVar('w2a_request', default=None)


def phase(name):
    state = CURRENT_REQUEST.get()
    if state:
        state.check()
        state.phase = name


def send_attempted():
    state = CURRENT_REQUEST.get()
    if state:
        state.check()
        if state.send_attempt_count:
            raise RuntimeError('A second submission attempt is forbidden for this request')
        state.send_attempt_count += 1
        state.phase = 'send'
        state.send_state = 'unknown'


def send_confirmed():
    state = CURRENT_REQUEST.get()
    if state:
        state.send_state = 'confirmed'


def cleanup_allowed():
    state = CURRENT_REQUEST.get()
    return not state or (not asyncio.current_task().cancelling()
                         and state.remaining() > 0 and not state.guard.paused
                         and state.send_state != 'unknown' and not state.disconnected)
