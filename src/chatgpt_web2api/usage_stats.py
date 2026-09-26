"""Content-free REST attempt accounting, stored off the event loop in SQLite."""
from __future__ import annotations

import asyncio
import logging
import math
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)
PHASES = ('validation', 'queue', 'prepare', 'model_selection', 'navigation',
          'input', 'upload', 'send_ready', 'send', 'reply')
BUSINESS_TZ = timezone(timedelta(hours=8))


@dataclass
class UsageAttempt:
    owner: str
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: float = field(default_factory=time.time)
    clock_started: float = field(default_factory=time.monotonic)
    outcome: str = 'failed'
    response_bytes: int = 0
    stream: bool = False
    state: object = None

    def snapshot(self, request_bytes: int, http_status: int) -> dict:
        state = self.state
        timings = state.timing() if state else {}
        return {
            'request_id': state.request_id if state else self.request_id,
            'owner': self.owner, 'started_at': self.started_at, 'finished_at': time.time(),
            'status': self.outcome, 'http_status': http_status,
            'elapsed_seconds': timings.get('elapsed_seconds', time.monotonic() - self.clock_started),
            'request_bytes': max(0, request_bytes), 'response_bytes': self.response_bytes,
            'stream': int(self.stream),
            'phase': state.phase if state and state.phase in PHASES else 'validation',
            'send_state': state.send_state if state else 'not_sent',
            'preparation_retries': len(state.retry_codes) if state else 0,
            'reply_recoveries': state.reply_recovery_attempt_count if state else 0,
            **{f'{name}_seconds': timings.get('phase_timings', {}).get(name) for name in PHASES},
        }


class UsageStreamResponse(web.StreamResponse):
    """Count application SSE body bytes accepted by aiohttp, including errors/DONE."""

    def __init__(self, *args, usage: UsageAttempt | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._usage_attempt = usage
        if usage:
            usage.stream = True

    async def write(self, data):
        await super().write(data)
        if self._usage_attempt:
            self._usage_attempt.response_bytes += len(data)

    async def write_eof(self, data=b''):
        already_sent = self._eof_sent
        await super().write_eof(data)
        if self._usage_attempt and not already_sent:
            self._usage_attempt.response_bytes += len(data)


class UsageStore:
    """One writer thread, a bounded queue, and key-scoped aggregate reads.

    Storage failures must never change chat results. Callers can inspect the
    queue/error counters; no prompt, key, IP, model input or conversation ID is
    accepted by the schema. Only terminal attempts are persisted.
    """

    def __init__(self, path: str, retention_days: int = 90):
        self.path = path
        self.retention_days = retention_days
        self.available = False
        self.dropped_records = 0
        self.write_errors = 0
        self.inflight: dict[str, UsageAttempt] = {}
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self._executor = None
        self._writer = None
        self._writing = False
        self._connection = None
        self._last_pruned = 0

    async def _run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    async def start(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='usage-store')
        try:
            await self._run(self._open)
        except Exception as exc:
            logger.error('Usage storage unavailable (%s); chat remains available', type(exc).__name__)
            return
        self.available = True
        self._writer = asyncio.create_task(self._write_loop())

    def _open(self):
        if self.path != ':memory:':
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = str(Path(self.path).expanduser()) if self.path != ':memory:' else self.path
        self._connection = sqlite3.connect(path, timeout=1)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute('PRAGMA journal_mode=WAL')
        phase_columns = ', '.join(f'{name}_seconds REAL' for name in PHASES)
        self._connection.execute(f'''CREATE TABLE IF NOT EXISTS attempts (
            request_id TEXT PRIMARY KEY, owner TEXT NOT NULL, started_at REAL NOT NULL,
            finished_at REAL NOT NULL, status TEXT NOT NULL, http_status INTEGER NOT NULL,
            elapsed_seconds REAL NOT NULL, request_bytes INTEGER NOT NULL,
            response_bytes INTEGER NOT NULL, stream INTEGER NOT NULL,
            phase TEXT NOT NULL, send_state TEXT NOT NULL,
            preparation_retries INTEGER NOT NULL, reply_recoveries INTEGER NOT NULL,
            {phase_columns})''')
        self._connection.execute('CREATE INDEX IF NOT EXISTS attempts_owner_time ON attempts(owner, started_at)')
        self._connection.execute('CREATE INDEX IF NOT EXISTS attempts_time ON attempts(started_at)')
        self._connection.execute('CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value REAL)')
        self._connection.execute("INSERT OR IGNORE INTO metadata VALUES ('created_at', ?)", (time.time(),))
        self._prune(time.time())
        self._connection.commit()

    def _prune(self, now):
        self._connection.execute('DELETE FROM attempts WHERE started_at < ?',
                                 (now - self.retention_days * 86400,))
        self._last_pruned = now

    def begin(self, owner):
        attempt = UsageAttempt(owner)
        self.inflight[attempt.request_id] = attempt
        return attempt

    def finish(self, attempt, request_bytes, http_status):
        self.inflight.pop(attempt.request_id, None)
        if not self.available:
            self.dropped_records += 1
            return
        try:
            self.queue.put_nowait(attempt.snapshot(request_bytes, http_status))
        except asyncio.QueueFull:
            self.dropped_records += 1

    def _insert(self, record):
        now = time.time()
        with self._connection:
            if now - self._last_pruned >= 3600:
                self._prune(now)
            columns = ','.join(record)
            placeholders = ','.join('?' for _ in record)
            self._connection.execute(f'INSERT OR IGNORE INTO attempts ({columns}) VALUES ({placeholders})',
                                     tuple(record.values()))

    async def _write_loop(self):
        while True:
            record = await self.queue.get()
            self._writing = True
            try:
                await self._run(self._insert, record)
            except Exception as exc:
                self.write_errors += 1
                logger.error('Usage record could not be saved (%s)', type(exc).__name__)
            finally:
                self._writing = False
                self.queue.task_done()

    def health(self):
        return {'available': self.available, 'pending_records': self.queue.qsize() + int(self._writing),
                'dropped_records': self.dropped_records, 'write_errors': self.write_errors,
                'retention_days': self.retention_days}

    async def report(self, owner, period='today'):
        if period not in ('today', '7d', '30d'):
            raise ValueError('period must be today, 7d or 30d')
        if not self.available:
            raise RuntimeError('Usage storage unavailable')
        # Keep the float cutoff at the same precision as recorded time.time().
        # datetime rounds to microseconds and can exclude a same-tick record.
        now_seconds = time.time()
        now = datetime.fromtimestamp(now_seconds, BUSINESS_TZ)
        days = {'today': 1, '7d': 7, '30d': 30}[period]
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
        report = await self._run(self._report, owner, start.timestamp(), now_seconds)
        rows = {row['date']: row for row in report['daily']}
        report['daily'] = [rows.get((start + timedelta(days=i)).date().isoformat(), {
            'date': (start + timedelta(days=i)).date().isoformat(),
            'total': 0, 'succeeded': 0, 'failed': 0, 'cancelled': 0,
            'request_bytes': 0, 'response_bytes': 0}) for i in range(days)]
        active = [a for a in self.inflight.values() if a.owner == owner]
        report.update({'period': period, 'timezone': 'Asia/Shanghai', 'generated_at': now_seconds,
                       'live': {'active': sum(not a.state or a.state.phase != 'queue' for a in active),
                                'queued': sum(bool(a.state and a.state.phase == 'queue') for a in active)},
                       'storage': self.health()})
        return report

    def _report(self, owner, start, end):
        conn = self._connection
        where = 'FROM attempts WHERE owner = ? AND started_at >= ? AND started_at <= ?'
        args = (owner, max(start, end - self.retention_days * 86400), end)
        aggregates = '''COUNT(*) AS total, COALESCE(SUM(status = 'succeeded'),0) AS succeeded,
            COALESCE(SUM(status = 'failed'),0) AS failed, COALESCE(SUM(status = 'cancelled'),0) AS cancelled,
            COALESCE(SUM(request_bytes),0) AS request_bytes, COALESCE(SUM(response_bytes),0) AS response_bytes'''
        summary = dict(conn.execute(f'''SELECT {aggregates}, AVG(elapsed_seconds) AS average_seconds,
            COALESCE(SUM(preparation_retries),0) AS preparation_retries,
            COALESCE(SUM(reply_recoveries),0) AS reply_recoveries {where}''', args).fetchone())
        count = summary['total']
        summary['success_rate'] = summary['succeeded'] / count if count else None
        summary['p95_seconds'] = (conn.execute(f'SELECT elapsed_seconds {where} ORDER BY elapsed_seconds LIMIT 1 OFFSET ?',
            (*args, math.ceil(count * .95) - 1)).fetchone()[0] if count else None)
        daily = [dict(row) for row in conn.execute(f'''SELECT date(started_at, 'unixepoch', '+8 hours') AS date,
            {aggregates} {where} GROUP BY date ORDER BY date''', args)]
        recent = [dict(row) for row in conn.execute(f'''SELECT request_id, started_at, status, http_status,
            elapsed_seconds, request_bytes, response_bytes, stream, phase, send_state,
            preparation_retries, reply_recoveries {where} ORDER BY started_at DESC LIMIT 50''', args)]
        phases = dict(conn.execute('SELECT ' + ','.join(f'AVG({name}_seconds) AS {name}' for name in PHASES)
                                  + ' ' + where, args).fetchone())
        return {'summary': summary, 'daily': daily, 'recent': recent,
                'phase_averages': phases,
                'recording_since': conn.execute("SELECT value FROM metadata WHERE name='created_at'").fetchone()[0]}

    async def close(self):
        if self._writer:
            with suppress(TimeoutError):
                await asyncio.wait_for(self.queue.join(), timeout=5)
            self._writer.cancel()
            with suppress(asyncio.CancelledError):
                await self._writer
        if self._connection:
            await self._run(self._connection.close)
        if self._executor:
            self._executor.shutdown(wait=False)
        self.available = False
