"""Bounded, credential-scoped progress for existing JSON and SSE chat requests.

All operations run synchronously on the REST event loop. Polling never acquires
a browser or sends a message. Completed entries retain only sanitized snapshots.
"""
from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass

from .request_guard import RequestState


class ProgressConflictError(ValueError):
    pass


class ProgressCapacityError(RuntimeError):
    pass


def normalize_progress_id(value: str) -> str:
    if not isinstance(value, str) or len(value) not in (32, 36):
        raise ValueError('progress_id must be a UUID string')
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ValueError('progress_id must be a UUID string') from None


@dataclass
class _Entry:
    state: RequestState | None
    snapshot: dict | None = None
    expires_at: float | None = None


class RequestProgressStore:
    def __init__(self, *, capacity: int = 256, retention_seconds: float = 300):
        self.capacity = capacity
        self.retention_seconds = retention_seconds
        self._entries: dict[tuple[str, str], _Entry] = {}
        self._active: dict[str, _Entry] = {}

    def _prune(self):
        now = time.monotonic()
        for key, entry in list(self._entries.items()):
            if entry.expires_at is not None and entry.expires_at <= now:
                del self._entries[key]

    def register(self, owner: str, progress_id: str, state: RequestState):
        self._prune()
        key = (owner, progress_id)
        if key in self._entries:
            raise ProgressConflictError('progress_id is already registered; use a new UUID per attempt')
        if len(self._entries) >= self.capacity:
            raise ProgressCapacityError('Progress capacity reached; wait for completed records to expire')
        state.progress_id = progress_id
        entry = _Entry(state)
        self._entries[key] = entry
        self._active[state.request_id] = entry

    @staticmethod
    def _snapshot(state: RequestState, status: str) -> dict:
        result = {'object': 'chat.request.progress',
                  'status': status,
                  **state.diagnostics(succeeded=status == 'succeeded'),
                  'send_state': state.send_state,
                  'remaining_seconds': round(state.remaining(), 3) if status == 'running' else 0}
        if status == 'running':
            result['terminal_reason'] = None
        return result

    def get(self, owner: str, progress_id: str) -> dict | None:
        self._prune()
        entry = self._entries.get((owner, progress_id))
        if entry is None:
            return None
        if entry.state is not None:
            return self._snapshot(entry.state, 'running')
        return copy.deepcopy(entry.snapshot)

    def finish(self, request_id: str, status: str):
        entry = self._active.pop(request_id, None)
        if entry is not None:
            entry.state.finish()
            entry.snapshot = self._snapshot(entry.state, status)
            entry.state = None  # Do not retain drivers, browser state or SSE transports.
            entry.expires_at = time.monotonic() + self.retention_seconds
