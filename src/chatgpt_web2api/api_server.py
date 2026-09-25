"""OpenAI-compatible API server.

Endpoints:
  POST /v1/chat/completions  — chat (streaming + non-streaming)
  GET  /v1/models            — model catalog
  GET  /v1/projects          — ChatGPT projects
  GET  /health               — health + Chrome status
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import uuid
from contextlib import asynccontextmanager, suppress

from aiohttp import web

from .breakers import BreakerKind, BreakerRegistry, CircuitOpenError
from .cdp_driver import (
    AuthExpiredError,
    CDPDriver,
    GenerationStuckError,
    NavigationError,
    RateLimitError,
    is_rate_limited_text,
)
from .config import Config
from .cross_process_lock import LockAcquisitionError
from .image_input import ImageInputError, normalize_messages
from .lock_resolver import MutationLock, OwnedTabRequiredError, resolve_mutation_lock
from .request_guard import CURRENT_REQUEST, BrowserGuard, BrowserPausedError, RequestState, phase
from .resilience import retry_on_rate_limit
from .rest_driver_pool import RestDriverPool, RestPoolBusyError

logger = logging.getLogger(__name__)

# Model mapping: user-facing names → ChatGPT web slugs
MODEL_MAP = {
    "gpt-5.5": "gpt-5-5",
    "gpt-5.5-thinking": "gpt-5-5-thinking",
    "gpt-5.3": "gpt-5-3",
    "gpt-5.2": "gpt-5-2",
    "gpt-5.1": "gpt-5-1",
    "gpt-5": "gpt-5",
    "gpt-5-mini": "gpt-5-mini",
    "gpt-5.3-mini": "gpt-5-3-mini",
    "auto": "auto",
    # Legacy aliases
    "gpt-4o": "auto",
    "gpt-4": "gpt-5",
    "gpt-3.5-turbo": "gpt-5-mini",
}


class APIServer:
    """OpenAI-compatible API backed by CDP automation."""

    def __init__(
        self, config: Config, driver: CDPDriver, breakers: BreakerRegistry | None = None
    ) -> None:
        self._config = config
        self._driver = driver
        self._cdp_port = config.chrome.cdp_port
        self._parallel_tabs = config.chatgpt.parallel_tabs
        self._request_count = 0
        # Health telemetry (event-derived, not polled). These are the only
        # fields that make sense to cache: they mark WHEN something happened,
        # not whether something is alive right now (that's computed live in
        # _handle_health). Without last_successful_send_at, a zombie process
        # that never connected (cdp_connected=false, requests_served=0) looks
        # identical to a freshly-started healthy one — both report "waiting".
        self._started_at = time.time()
        self._last_error: str | None = None
        self._last_error_at: float | None = None
        self._browser_guard = BrowserGuard()
        self._last_successful_send_at: float | None = None
        # Non-rate-limit breaker registry (Phase 4). Injected by Service so the
        # REST process shares one registry across Chrome + driver + server.
        # Default-constructed for back-compat with tests that don't pass one.
        self._breakers = breakers or BreakerRegistry()
        # Track last conversation for multi-turn continuity
        self._last_conv_id: str | None = None
        self._last_project_id: str | None = None
        self._driver_pool: RestDriverPool | None = None

        self.app = web.Application(client_max_size=10 * 1024 * 1024)
        self.app.router.add_post("/v1/chat/completions", self._handle_chat)
        self.app.router.add_post("/chat/completions", self._handle_chat)
        self.app.router.add_post("/v1/browser/recover", self._handle_browser_recover)
        self.app.router.add_get("/v1/models", self._handle_models)
        self.app.router.add_get("/v1/projects", self._handle_projects)
        self.app.router.add_get("/health", self._handle_health)
        self.app.router.add_get("/", self._handle_health)

    async def start_pool(self):
        if self._config.server.rest_pool_size > 1:
            self._driver_pool = await RestDriverPool.start(self._config, self._driver)

    async def close_pool(self):
        if self._driver_pool is not None:
            await self._driver_pool.close()

    @property
    def _request_driver(self):
        state = CURRENT_REQUEST.get()
        return state.worker.driver if state and state.worker is not None else self._driver

    @property
    def _request_breakers(self):
        state = CURRENT_REQUEST.get()
        return state.worker.breakers if state and state.worker is not None else self._breakers

    @property
    def _request_guard(self):
        state = CURRENT_REQUEST.get()
        return state.guard if state else self._browser_guard

    @asynccontextmanager
    async def _chat_worker(self, conversation_id):
        pool = getattr(self, '_driver_pool', None)
        if pool is None:
            yield
            return
        phase('queue')
        async with pool.acquire(conversation_id) as worker:
            state = CURRENT_REQUEST.get()
            state.worker = worker
            state.guard = worker.guard
            try:
                yield
            except BaseException as exc:
                self._record_request_failure(exc)
                raise
            finally:
                # A failed new-chat send may already have obtained its ID.
                # Keep that ID pinned to its paused worker, too.
                if state.send_attempt_count and not worker.conversation_id:
                    conv_id = worker.driver._current_conv_id
                    if conv_id:
                        pool.bind_conversation(worker, conv_id)

    def _remember_conversation(self, conv_id):
        state = CURRENT_REQUEST.get()
        if state and state.worker is not None:
            if conv_id:
                self._driver_pool.bind_conversation(state.worker, conv_id)
        else:
            self._last_conv_id = conv_id

    # ── Auth ──────────────────────────────────────────────────

    def _check_auth(self, request: web.Request) -> web.Response | None:
        """Check API key if configured. Returns error response or None."""
        keys = self._config.server.api_keys
        if not keys:
            return None
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            key = auth[7:]
        else:
            key = request.query.get("key", "")
        if key not in keys:
            return web.json_response(
                {"error": {"message": "Invalid API key", "type": "auth_error"}},
                status=401,
            )
        return None

    # ── Handlers ──────────────────────────────────────────────

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Honest health endpoint — observes current reality, not a stale mirror.

        The old version returned ``"waiting"`` when CDP was disconnected, which
        is indistinguishable from "freshly started, connecting now" — a zombie
        process (HTTP listener up, CDP never connected) reported the same
        status as a healthy one. This version distinguishes four states:

        - ``starting``: listener up, driver not yet connected, never served
        - ``healthy``: Chrome alive AND driver connected
        - ``degraded``: Chrome alive but driver disconnected (zombie/recovering)
        - ``broken``: Chrome itself unreachable

        Live fields (chrome_running, driver_connected) are computed fresh on
        each call — /health is infrequent (supervisor poll), and cached state
        would lag reality. Event-derived fields (started_at, last_error,
        last_successful_send_at, requests_served) are tracked on the instance.
        """
        import urllib.request

        pool = self._driver_pool
        guards = [w.guard for w in pool.workers] if pool else [self._browser_guard]
        registries = [self._breakers] + ([w.breakers for w in pool.workers] if pool else [])
        driver_connected = (all(w.driver.is_connected for w in pool.workers)
                            if pool else bool(self._driver.is_connected))

        # Chrome liveness: cheap HTTP GET to /json/version. If Chrome is dead,
        # this fails fast (connection refused). Run synchronously — /health is
        # infrequent and the call is sub-millisecond on loopback.
        chrome_running = False
        try:
            loop = asyncio.get_event_loop()

            def _probe():
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{self._cdp_port}/json/version", timeout=2
                    ) as r:
                        return r.status == 200
                except Exception:
                    return False

            chrome_running = await loop.run_in_executor(None, _probe)
        except Exception:
            chrome_running = False

        # Status logic — zombie case (Chrome up, driver dead) is "degraded",
        # never "ok"/"waiting". The old "waiting" non-answer is gone.
        if not chrome_running:
            status = "broken"
        elif not driver_connected:
            status = "degraded"
        elif self._last_successful_send_at is None and self._request_count == 0:
            status = "starting"
        else:
            status = "healthy"

        # An open breaker can only DOWNGRADE starting|healthy -> degraded. It
        # must never override "broken" (Chrome down is a harder failure than a
        # tripped circuit) and never force "broken" — auth_required is serious,
        # but "broken" invites a destructive supervisor restart, while
        # "degraded" correctly signals "up but refusing some/all traffic". A
        # disconnect-degraded stays degraded (not worse).
        if status in ("starting", "healthy") and any(r.first_open() for r in registries):
            status = "degraded"

        # Current-state summary, distinct from the historical/latching last_error.
        open_kinds = [k.value for k in BreakerKind if any(r.is_open(k) for r in registries)]
        if status in ('starting', 'healthy') and (
            any(g.paused or g.consecutive_timeouts for g in guards)
            or (pool and pool.snapshot()['account_retry_after'] > 0)
        ):
            status = 'degraded'

        return web.json_response(
            {
                "status": status,
                "chrome_running": chrome_running,
                "cdp_connected": driver_connected,
                "driver_connected": driver_connected,
                "requests_served": self._request_count,
                "started_at": self._started_at,
                "last_successful_send_at": self._last_successful_send_at,
                "last_error": self._last_error,
                "last_error_at": self._last_error_at,
                "browser_paused": all(g.paused for g in guards),
                "browser_pause_reason": next((g.reason for g in guards if g.paused), None),
                "consecutive_cdp_timeouts": max(g.consecutive_timeouts for g in guards),
                "open_breakers": open_kinds,
                "breakers": self._breakers.snapshot(),
                "rest_pool": pool.snapshot() if pool else {"enabled": False, "size": 1},
            }
        )

    async def _handle_models(self, request: web.Request) -> web.Response:
        if err := self._check_auth(request):
            return err
        if self._browser_guard.paused:
            return self._error_response(BrowserPausedError('Browser operations paused'))
        try:
            async with self._read_driver() as driver:
                raw = await driver.get_models()
        except Exception:
            raw = []

        models = []
        for m in raw:
            slug = m.get("slug", "")
            models.append(
                {
                    "id": slug,
                    "object": "model",
                    "created": 1700000000,
                    "owned_by": "chatgpt-web",
                }
            )

        if not models:
            for slug in ["auto", "gpt-5-5", "gpt-5-mini"]:
                models.append(
                    {
                        "id": slug,
                        "object": "model",
                        "created": 1700000000,
                        "owned_by": "chatgpt-web",
                    }
                )

        return web.json_response({"object": "list", "data": models})

    async def _handle_projects(self, request: web.Request) -> web.Response:
        if err := self._check_auth(request):
            return err
        if self._browser_guard.paused:
            return self._error_response(BrowserPausedError('Browser operations paused'))
        try:
            async with self._read_driver() as driver:
                projects = await driver.get_projects()
        except Exception as e:
            logger.error("Failed to get projects: %s", e)
            projects = []
        return web.json_response({"object": "list", "data": projects})

    @asynccontextmanager
    async def _read_driver(self):
        if self._driver_pool is None:
            yield self._driver
        else:
            async with asyncio.timeout(self._config.server.request_timeout):
                async with self._driver_pool.acquire(None) as worker:
                    yield worker.driver

    async def _handle_chat(self, request: web.Request) -> web.Response:
        state = RequestState(asyncio.get_running_loop().time() + self._config.server.request_timeout,
                             BrowserGuard() if getattr(self, '_driver_pool', None) else self._browser_guard)
        token = CURRENT_REQUEST.set(state)
        task = asyncio.current_task()
        transport = getattr(request, 'transport', None)

        async def watch_disconnect():
            while True:
                await asyncio.sleep(0.1)
                if transport.is_closing():
                    state.disconnected = True
                    task.cancel()
                    return

        watcher = asyncio.create_task(watch_disconnect()) if isinstance(transport, asyncio.BaseTransport) else None
        try:
            async with asyncio.timeout_at(state.deadline):
                response = await self._handle_chat_impl(request)
            if response.status < 400 and not state.failed:
                state.terminal_reason = 'succeeded'
                self._last_error = None
                self._last_error_at = None
            if isinstance(response, web.Response) and response.content_type == 'application/json':
                body = json.loads(response.body)
                body['request_diagnostics'] = state.diagnostics(
                    succeeded=response.status < 400 and not state.failed)
                response.body = json.dumps(body).encode()
            return response
        except TimeoutError as exc:
            state.deadline_expired = True
            self._record_request_failure(exc)
            return await self._guard_failure_response(exc)
        except asyncio.CancelledError as exc:
            state.terminal_reason = 'cancelled'
            self._record_request_failure(exc)
            if state.disconnected:
                return self._error_response(TimeoutError('Client disconnected; request stopped'))
            raise
        finally:
            logger.info('Request terminal: %s', json.dumps(state.diagnostics()))
            if watcher:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
            CURRENT_REQUEST.reset(token)

    def _record_request_failure(self, exc):
        state = CURRENT_REQUEST.get()
        pool = getattr(self, '_driver_pool', None)
        if pool is not None and isinstance(exc, RateLimitError) and state and state.worker is not None:
            pool.throttle(exc.retry_after)
        if state and not state.failed:
            state.failed = True
            # Applied while holding the mutation lock, before queued callers
            # can begin another navigation on a possibly unresolved browser.
            if state.send_state == 'unknown':
                state.guard.pause('send_outcome_unknown')
            elif state.phase not in ('validation', 'queue') and (
                isinstance(exc, asyncio.CancelledError) or state.remaining() <= 0
            ):
                state.guard.pause('request_interrupted')
            elif state.worker is not None and state.send_state == 'confirmed':
                state.guard.pause('reply_outcome_unresolved')
        self._last_error = f'{type(exc).__name__}: {exc}'
        self._last_error_at = time.time()

    @asynccontextmanager
    async def _request_lock(self, port, key):
        phase('queue')
        async with MutationLock(port, key):
            try:
                yield
            except BaseException as exc:
                self._record_request_failure(exc)
                raise

    async def _handle_browser_recover(self, request):
        err = self._check_auth(request)
        if err is not None:
            return err
        if self._driver_pool is not None:
            return await self._recover_pool(request)
        acquired = False
        try:
            async with asyncio.timeout(5):
                port, key = resolve_mutation_lock(self._driver, self._parallel_tabs)
                async with MutationLock(port, key):
                    acquired = True
                    result = await self._driver._js_strict("'w2a-responsive'", timeout=3)
                    if result != 'w2a-responsive':
                        raise BrowserPausedError('Browser probe did not return the expected result')
                    self._browser_guard.recover()
            return web.json_response({'status': 'ready', 'prompt_sent': False,
                                      'note': 'Read-only probe passed; no previous request was replayed'})
        except Exception:
            if not acquired:
                return web.json_response({'error': {'code': 'browser_busy',
                    'message': 'Recovery could not acquire the browser lock; active work was not interrupted',
                    'prompt_sent': False}}, status=503)
            self._browser_guard.pause('recovery_probe_failed')
            return web.json_response({'error': {'code': 'browser_unresponsive',
                'message': 'Read-only probe failed; inspect the browser before trying again',
                'prompt_sent': False}}, status=503)

    async def _recover_pool(self, request):
        pool = self._driver_pool
        selected = request.query.get('slot')
        try:
            indices = [int(selected)] if selected is not None else [
                w.index for w in pool.workers if w.guard.paused
            ]
            if not indices:
                indices = [w.index for w in pool.workers]
            if any(i < 0 or i >= len(pool.workers) for i in indices):
                raise ValueError
        except ValueError:
            return web.json_response({'error': {'code': 'invalid_browser_slot',
                'message': 'slot must be a worker index from /health', 'prompt_sent': False}}, status=400)
        recovered = []
        for index in indices:
            acquired = False
            try:
                async with asyncio.timeout(5):
                    async with pool.recovery(index) as worker:
                        port, key = resolve_mutation_lock(worker.driver, True)
                        async with MutationLock(port, key):
                            acquired = True
                            try:
                                result = await worker.driver._js_strict("'w2a-responsive'", timeout=3)
                                if result != 'w2a-responsive':
                                    raise BrowserPausedError('Unexpected browser probe result')
                                worker.guard.recover()
                            except BaseException:
                                worker.guard.pause('recovery_probe_failed')
                                raise
                recovered.append(index)
            except Exception:
                return web.json_response({'error': {
                    'code': 'browser_unresponsive' if acquired else 'browser_busy',
                    'message': 'Read-only recovery failed; active work was not interrupted',
                    'browser_slot': index, 'prompt_sent': False},
                    'recovered_slots': recovered}, status=503)
        return web.json_response({'status': 'ready', 'prompt_sent': False,
            'recovered_slots': recovered,
            'note': 'No previous request was replayed; account cooldown is unchanged'})

    async def _guard_failure_response(self, exc):
        response = self._error_response(exc)
        state = CURRENT_REQUEST.get()
        if not state or state.sse_response is None:
            return response
        # HTTP headers are already sent. Emit a structured error event, never
        # a second HTTP response or a successful stop chunk.
        resp = state.sse_response
        try:
            async with asyncio.timeout(1):
                await self._send_sse(resp, {**json.loads(response.body), 'choices': [
                    {'index': 0, 'delta': {}, 'finish_reason': 'error'}]})
                await resp.write(b'data: [DONE]\n\n')
                await resp.write_eof()
        except (Exception, asyncio.CancelledError):
            pass  # Client may already have closed its socket.
        return resp

    async def _handle_chat_impl(self, request: web.Request) -> web.Response:
        if err := self._check_auth(request):
            return err

        self._request_count += 1

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"error": {"message": "Invalid JSON", "type": "invalid_request_error"}},
                status=400,
            )

        if not isinstance(body, dict):
            return web.json_response({"error": {"message": "Request body must be an object", "type": "invalid_request_error"}}, status=400)
        try:
            messages, images = normalize_messages(body.get("messages", []))
        except ImageInputError as exc:
            return web.json_response({"error": {"message": str(exc), "type": "invalid_request_error"}}, status=400)
        if not messages:
            return web.json_response(
                {"error": {"message": "No messages provided", "type": "invalid_request_error"}},
                status=400,
            )

        model = body.get("model", self._config.chatgpt.default_model)
        stream = body.get("stream", False)
        CURRENT_REQUEST.get().allow_preparation_recovery = not bool(stream) and bool(body.get('conversation_id'))
        project_id = (
            body.get("project_id")
            or body.get("gizmo_id")
            or (body.get("metadata", {}) or {}).get("project_id")
            or self._config.chatgpt.default_project_id
        )
        conversation_id = body.get("conversation_id")
        new_conversation = body.get("new_conversation", False)
        invalid = None
        if not isinstance(new_conversation, bool):
            invalid = "new_conversation must be a JSON boolean"
        elif conversation_id is not None and (
            not isinstance(conversation_id, str) or not conversation_id.strip()
        ):
            invalid = "conversation_id must be a nonempty string or null"
        elif new_conversation and conversation_id:
            invalid = "new_conversation=true cannot be combined with conversation_id"
        if invalid:
            return web.json_response(
                {"error": {"message": invalid, "type": "invalid_request_error"}},
                status=400,
            )

        # Build conversation text from all messages
        # Includes prior assistant context for stateless clients (OpenAI SDK)
        system_parts = []
        conversation_lines = []
        user_msg_count = 0
        MAX_HISTORY_TURNS = 10  # Cap to avoid textarea overflow

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in content
                )
            else:
                content = str(content)

            if role == "system":
                system_parts.append(content)
            elif role == "user":
                conversation_lines.append(f"[User]\n{content}")
                user_msg_count += 1
            elif role == "assistant":
                conversation_lines.append(f"[Assistant]\n{content}")

        # Trim to last N turns if too many messages
        if len(conversation_lines) > MAX_HISTORY_TURNS * 2:
            conversation_lines = conversation_lines[-(MAX_HISTORY_TURNS * 2) :]

        # Verify at least one user message exists
        if user_msg_count == 0:
            return web.json_response(
                {"error": {"message": "No user message", "type": "invalid_request_error"}},
                status=400,
            )

        # Compose final text
        prefix = ""
        if system_parts:
            prefix = "[System Instructions]\n" + "\n".join(system_parts) + "\n\n"
        full_text = prefix + "\n".join(conversation_lines)

        model_slug = MODEL_MAP.get(model, model)
        timeout = self._config.server.request_timeout

        logger.info(
            "Request #%d: model=%s->%s conv=%s project=%s stream=%s msg=%.60s",
            self._request_count,
            model,
            model_slug,
            conversation_id,
            project_id,
            stream,
            full_text,
        )

        # Serialize — cross-process lock so MCP + REST don't corrupt each other
        try:
            async with self._chat_worker(conversation_id):
                # Circuit-open fail-fast (Phase 4 PR2): refuse before touching Chrome
                self._request_guard.check()
                # if a breaker is open. Placed inside the try so it flows through
                # the except below → _error_response + _last_error, consistent with
                # every other failure path. Checked before acquiring the lock so a
                # process that already knows it will refuse doesn't block on the
                # browser lock. If AUTH_EXPIRED is open, probes auth recovery first
                # (the user may have logged back in).
                await self._check_circuit_or_recover()

                # PR4/5: per-target lock in parallel mode (port-wide otherwise).
                # Resolver raises OwnedTabRequiredError (→ 503) if parallel mode
                # has no owned target rather than silently degrading to the port
                # lock (split-brain guard). When parallel mode is OFF, skip the
                # resolver entirely and use the cached port — preserves the exact
                # legacy path (the resolver would read driver.port, which is the
                # same value but needlessly couples the legacy path to the driver).
                if self._parallel_tabs:
                    _port, _key = resolve_mutation_lock(self._request_driver, True)
                else:
                    _port, _key = self._cdp_port, None
                async with self._request_lock(_port, _key):
                    self._request_guard.check()
                    phase('prepare')
                    CURRENT_REQUEST.get().preparation_attempt_count = 1
                    # Drift guard (parallel mode only): if the owned target changed
                    # while we waited for the lock, the key we hold no longer names
                    # the active tab. Fail retryably instead of mutating under a
                    # stale key.
                    if self._parallel_tabs:
                        _, _current_key = resolve_mutation_lock(self._request_driver, True)
                        if _current_key != _key:
                            raise OwnedTabRequiredError(
                                "owned target changed while waiting for mutation lock"
                            )
                    # Second circuit-open check, now that we hold the lock. A
                    # concurrent request may have tripped a breaker while we were
                    # waiting. Without this, we'd drive Chrome despite the process
                    # already knowing the circuit is open.
                    await self._check_circuit_or_recover()

                    # Select model if specified (non-fatal on failure)
                    if model_slug and model_slug != "auto":
                        phase('model_selection')
                        selected = await self._request_driver.select_model(model_slug)
                        if not selected:
                            logger.warning(
                                "Could not select model '%s', proceeding with active model",
                                model_slug,
                            )

                    # Decide: continue existing conversation or start fresh?
                    phase('navigation')
                    if conversation_id:
                        # Explicit conversation_id from client — navigate to it
                        await self._request_driver.navigate_conversation(conversation_id)
                    elif (
                        not new_conversation
                        and getattr(self, '_driver_pool', None) is None
                        and not images
                        and self._last_conv_id
                        and self._request_driver._current_conv_id == self._last_conv_id
                        and project_id == self._last_project_id
                        and not system_parts
                    ):
                        # Same session, same project, no system prompt override — continue.
                        # Reconcile against the live tab before sending: another process
                        # sharing the Chrome tab may have navigated it since our last turn,
                        # which would leave _current_conv_id stale. ensure_current_conversation
                        # verifies location.href and navigates back if needed (fail-closed).
                        logger.info("Continuing conversation: %s", self._last_conv_id)
                        await self._request_driver.ensure_current_conversation(self._last_conv_id)
                    else:
                        # Fresh chat
                        await self._request_driver.navigate_new_chat(gizmo_id=project_id)
                        if getattr(self, '_driver_pool', None) is None:
                            self._last_project_id = project_id

                    phase('prepare')
                    timeout = min(timeout, CURRENT_REQUEST.get().remaining())
                    if stream:
                        return await self._stream_response(request, model_slug, full_text, timeout, **({"images": images} if images else {}))
                    else:
                        return await self._full_response(request, model_slug, full_text, timeout, **({"images": images} if images else {}))

        except Exception as e:
            logger.error("Chat error: %s", e, exc_info=True)
            self._record_request_failure(e)
            return self._error_response(e)

    async def _check_circuit_or_recover(self) -> None:
        """Fail-fast if a breaker is open, with one exception: if AUTH_EXPIRED
        is the open breaker, probe auth recovery first (the user may have logged
        back in via the browser since the trip). If recovery succeeds the breaker
        is reset and the request proceeds; if it fails, or if a non-auth breaker
        is open, raise CircuitOpenError.

        Called at each fail-fast checkpoint (pre-lock, post-lock, streaming
        pre-prepare). Does NOT drive a chat send — recovery is a lightweight
        ``/api/auth/session`` token fetch via ``driver.recover_auth()``.
        """
        if self._request_breakers is not self._breakers:
            # The Chrome lifecycle breaker remains global; tab failures do not.
            global_kind = self._breakers.first_open()
            if global_kind is not None:
                raise CircuitOpenError(global_kind)
        open_kind = self._request_breakers.first_open()
        if open_kind is None:
            return
        if open_kind is BreakerKind.AUTH_EXPIRED:
            if await self._request_driver.recover_auth():
                # Auth restored — re-check in case another breaker is also open.
                open_kind = self._request_breakers.first_open()
                if open_kind is None:
                    return
        raise CircuitOpenError(open_kind)

    # ── Error mapping ─────────────────────────────────────────

    def _error_response(self, exc: Exception) -> web.Response:
        state = CURRENT_REQUEST.get()
        if isinstance(exc, (TimeoutError, BrowserPausedError)):
            code = 'browser_unresponsive' if isinstance(exc, BrowserPausedError) else 'browser_timeout'
            if state and state.disconnected:
                code = 'client_disconnected'
            elif isinstance(exc, TimeoutError) and state and state.remaining() <= 0:
                code = 'request_timeout'
            response = web.json_response({'error': {'code': code, 'type': 'server_error',
                'message': str(exc) or 'Request deadline exceeded'}},
                status=503 if isinstance(exc, BrowserPausedError) else 504)
        else:
            response = self._error_response_base(exc)
        if state:
            body = json.loads(response.body)
            body['error'].update(state.fields())
            body['request_diagnostics'] = state.diagnostics()
            response.body = json.dumps(body).encode()
        return response

    def _error_response_base(self, exc: Exception) -> web.Response:
        """Map a driver exception to an OpenAI-shaped error response.

        - RateLimitError → HTTP 429 with ``rate_limit_exceeded`` and a
          ``Retry-After`` cooldown. Clients must disable automatic replay.
        - AuthExpiredError → HTTP 401 ``invalid_api_key`` — the ChatGPT session
          expired; previously this surfaced as silent empty data or a generic
          timeout.
        - GenerationStuckError → HTTP 504 ``generation_stuck`` — the generation
          stalled (no DOM progress within the stall window); the phase is in the
          message for diagnosis.
        - Everything else stays a 500 ``server_error`` (a real failure, not
          retriable).
        """
        from .image_input import ImageUploadTimeout
        from .turn_anchor import TurnReconciliationError

        if isinstance(exc, RestPoolBusyError):
            return web.json_response({'error': {'message': str(exc),
                'type': 'server_error', 'code': 'browser_pool_busy'}}, status=503)

        if isinstance(exc, NavigationError):
            return web.json_response(
                {'error': {'message': str(exc), 'type': 'server_error',
                           'code': exc.code, 'navigation': exc.diagnostic}},
                status=504 if exc.code == 'navigation_timeout' else 502,
            )
        if isinstance(exc, ImageUploadTimeout):
            return web.json_response(
                {"error": {"message": str(exc), "type": "server_error",
                           "code": "image_upload_timeout", "prompt_sent": False}},
                status=504,
            )
        if isinstance(exc, TurnReconciliationError) and exc.diagnostic.get("reason") == "deadline_exceeded":
            return web.json_response(
                {"error": {"message": str(exc), "type": "server_error",
                           "code": "reply_timeout", "prompt_sent": True}},
                status=504,
            )
        if isinstance(exc, RateLimitError):
            retry_after = str(max(0, math.ceil(exc.retry_after)))
            return web.json_response(
                {
                    "error": {
                        "message": str(exc),
                        "type": "rate_limit_exceeded",
                        "param": None,
                        "code": "rate_limit_exceeded",
                    }
                },
                status=429,
                headers={"Retry-After": retry_after},
            )
        if isinstance(exc, AuthExpiredError):
            return web.json_response(
                {
                    "error": {
                        "message": str(exc),
                        "type": "invalid_api_key",
                        "param": None,
                        "code": "invalid_api_key",
                    }
                },
                status=401,
            )
        if isinstance(exc, GenerationStuckError):
            return web.json_response(
                {
                    "error": {
                        "message": str(exc),
                        "type": "server_error",
                        "param": None,
                        "code": "generation_stuck",
                    }
                },
                status=504,
            )
        if isinstance(exc, LockAcquisitionError):
            return web.json_response(
                {
                    "error": {
                        "message": str(exc),
                        "type": "server_error",
                        "param": None,
                        "code": "lock_timeout",
                    }
                },
                status=503,
            )
        if isinstance(exc, CircuitOpenError):
            return web.json_response(
                {
                    "error": {
                        "message": (
                            f"Circuit open for {exc.kind.value} — cooling down. Retry later."
                        ),
                        "type": "server_error",
                        "param": None,
                        "code": "circuit_open",
                    }
                },
                status=503,
            )
        if isinstance(exc, OwnedTabRequiredError):
            return web.json_response(
                {
                    "error": {
                        "message": f"{exc}. Retry later.",
                        "type": "server_error",
                        "param": None,
                        "code": "owned_tab_required",
                    }
                },
                status=503,
            )
        return web.json_response(
            {"error": {"message": str(exc), "type": "server_error"}},
            status=500,
        )

    # ── Response formatters ───────────────────────────────────

    async def _full_response(
        self, request: web.Request, model: str, text: str, timeout: float, *, images=None
    ) -> web.Response:
        """Non-streaming: collect the driver's verified final reply, return one JSON.

        The shared rate-limit wrapper propagates immediately in REST context;
        it must never replay this input/upload/send factory.
        """
        # P1: resolve model-aware detector budgets from config.
        from .completion_detector import DetectorBudgets

        budgets = DetectorBudgets.from_config(self._config.chatgpt, model)

        async def _send_and_collect() -> tuple[str, list[dict]]:
            collected = ""
            annotations = []
            async for chunk in self._request_driver.send_and_stream(
                text, timeout=timeout, budgets=budgets, model=model,
                **({"images": images} if images else {}),
            ):
                collected += chunk.delta
                if chunk.annotations:
                    annotations.extend(chunk.annotations)
            return collected, annotations

        # Do not replay uploads/sends after an image request hits a rate limit.
        full_text, annotations = await _send_and_collect() if images else await retry_on_rate_limit(self._request_driver, _send_and_collect)
        message = {"role": "assistant", "content": full_text}
        if annotations:
            message["annotations"] = annotations

        conv_id = self._request_driver._current_conv_id or ""
        self._remember_conversation(conv_id)
        self._last_successful_send_at = time.time()

        return web.json_response(
            {
                "id": f"chatcmpl-{uuid.uuid4().hex[:29]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "conversation_id": conv_id,
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        )

    async def _stream_response(
        self, request: web.Request, model: str, text: str, timeout: float, *, images=None
    ) -> web.Response:
        """Streaming: SSE framing with content buffered until final verification.

        Rate-limit handling for streaming is split, because once
        ``resp.prepare()`` commits the HTTP 200 status we can no longer send a
        429:

        - **Pre-flight** (before prepare): a single DOM scan. If throttled, we
          retry transparently (dismiss + backoff). If it persists, we return a
          proper 429 here while the status is still changeable.
        - **Mid-stream** (after prepare): a throttle is rare here (pre-flight
          cleared it), but if one occurs it falls back to the inline
          ``[Error: ...]`` SSE chunk — documented as a known limitation.
        """
        # P1: resolve model-aware detector budgets from config.
        from .completion_detector import DetectorBudgets

        budgets = DetectorBudgets.from_config(self._config.chatgpt, model)

        async def _preflight() -> None:
            """Raise RateLimitError if the pop-up is present right now."""
            try:
                scan = await self._request_driver._js_strict(
                    "(function(){var t=(document.body&&document.body.innerText)||'';"
                    "return JSON.stringify({text:t.slice(0,4000)});})()",
                    timeout=10,
                )
            except Exception:
                # CDP/JS error during scan — assume no rate limit (proceed).
                return
            try:
                body = json.loads(scan).get("text", "") if scan else ""
            except (json.JSONDecodeError, TypeError):
                body = ""
            if is_rate_limited_text(body):
                raise RateLimitError.from_text(body)

        # Transparent pre-flight retry — dismisses the pop-up and retries so a
        # transient limit never reaches the client as an error.
        try:
            await retry_on_rate_limit(self._request_driver, _preflight, max_attempts=3)
        except RateLimitError:
            # Persistent at pre-flight: still pre-prepare, so send a clean 429.
            raise

        # Circuit-open fail-fast (Phase 4 PR2): final check, after rate-limit
        # preflight but still before prepare() commits HTTP 200. A breaker may
        # have opened during model selection/navigation. After prepare() no
        # status change is possible, so this must stay pre-prepare.
        await self._check_circuit_or_recover()

        resp = web.StreamResponse()
        resp.content_type = "text/event-stream"
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["Connection"] = "keep-alive"
        await resp.prepare(request)
        state = CURRENT_REQUEST.get()
        if state:
            state.sse_response = resp

        cid = f"chatcmpl-{uuid.uuid4().hex[:29]}"
        created = int(time.time())

        # Role chunk
        await self._send_sse(
            resp,
            {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": ""},
                        "finish_reason": None,
                    }
                ],
            },
        )

        try:
            async for chunk in self._request_driver.send_and_stream(
                text, timeout=timeout, budgets=budgets, model=model,
                **({"images": images} if images else {}),
            ):
                if chunk.delta or chunk.annotations:
                    delta = {"content": chunk.delta}
                    if chunk.annotations:
                        delta["annotations"] = chunk.annotations
                    await self._send_sse(
                        resp,
                        {
                            "id": cid,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": delta,
                                    "finish_reason": None,
                                }
                            ],
                        },
                    )
                if chunk.finish_reason:
                    conv_id = self._request_driver._current_conv_id or ""
                    self._remember_conversation(conv_id)
                    if chunk.finish_reason == "stop":
                        self._last_successful_send_at = time.time()
                    await self._send_sse(
                        resp,
                        {
                            "id": cid,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "conversation_id": conv_id,
                            **({'request_diagnostics': CURRENT_REQUEST.get().diagnostics(
                                succeeded=chunk.finish_reason == 'stop')}
                               if CURRENT_REQUEST.get() else {}),
                            "choices": [
                                {"index": 0, "delta": {}, "finish_reason": chunk.finish_reason}
                            ],
                        },
                    )
        except (TimeoutError, BrowserPausedError) as e:
            self._record_request_failure(e)
            return await self._guard_failure_response(e)
        except RateLimitError as e:
            if CURRENT_REQUEST.get():
                self._record_request_failure(e)
                return await self._guard_failure_response(e)
            # Mid-stream throttle (rare after pre-flight). Status is locked at
            # 200, so we can't upgrade to 429; surface as an inline error chunk
            # with a recognizable marker so clients can detect it.
            logger.warning("Mid-stream rate limit: %s", e)
            await self._send_sse(
                resp,
                {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "content": f"\n\n[Error: rate_limit_exceeded — retry in {e.retry_after}s]"
                            },
                            "finish_reason": "error",
                        }
                    ],
                },
            )
        except AuthExpiredError as e:
            if CURRENT_REQUEST.get():
                self._record_request_failure(e)
                return await self._guard_failure_response(e)
            # Session expired mid-stream (status locked at 200). Surface with a
            # recognizable marker so clients can prompt re-login.
            logger.warning("Mid-stream auth expiry")
            await self._send_sse(
                resp,
                {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "\n\n[Error: auth_expired — re-login required]"},
                            "finish_reason": "error",
                        }
                    ],
                },
            )
        except GenerationStuckError as e:
            if CURRENT_REQUEST.get():
                self._record_request_failure(e)
                return await self._guard_failure_response(e)
            # Generation stalled mid-stream (status locked at 200). Surface the
            # phase + duration so the client can decide whether to retry.
            logger.warning("Mid-stream generation stuck: %s", e)
            await self._send_sse(
                resp,
                {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "content": f"\n\n[Error: generation_stuck — stalled in {e.phase} for {e.stalled_for_s:.0f}s]"
                            },
                            "finish_reason": "error",
                        }
                    ],
                },
            )
        except Exception as e:
            logger.error("Stream error: %s", e)
            if CURRENT_REQUEST.get():
                self._record_request_failure(e)
                return await self._guard_failure_response(e)
            await self._send_sse(
                resp,
                {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": f"\n\n[Error: {e}]"},
                            "finish_reason": "error",
                        }
                    ],
                },
            )

        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp

    @staticmethod
    async def _send_sse(resp: web.StreamResponse, data: dict) -> None:
        await resp.write(f"data: {json.dumps(data)}\n\n".encode())
