"""Read-only, per-upload confirmation for ChatGPT's local image previews.

Correlate the exact generated filename and file ID in process_upload_stream,
then require its completed event in a fully received successful response.
Neither the local preview nor HTTP 200 alone proves processing completed.
"""
from __future__ import annotations

import base64
import json
from urllib.parse import urlsplit

from .image_input import ImageUploadError


def upload_completed(wire: str, file_id: str) -> bool:
    """Accept NDJSON and single-line SSE data frames; ignore other file IDs."""
    events = []
    for line in wire.splitlines():
        value = line.removeprefix('data:').strip()
        if not value or value == '[DONE]' or value.startswith('event:'):
            continue
        try:
            item = json.loads(value)
        except (TypeError, ValueError):
            return False
        if not isinstance(item, dict):
            return False
        if item.get('file_id') == file_id:
            if item.get('error') or item.get('event') in ('file.processing.failed', 'file.processing.error'):
                return False
            events.append(item.get('event'))
    return bool(events) and events[-1] == 'file.processing.completed'


class UploadAcknowledgements:
    def __init__(self, driver, names):
        self.driver = driver
        self.names = set(names)
        self.socket = getattr(driver, '_ws', None)
        self.target = getattr(driver, '_target_id', None)
        self.requests = {}
        self.statuses = {}
        self.finished = set()
        self.confirmed = set()
        self.checked = set()
        self.bindings = {}
        self.handlers = []

    def attach(self):
        table = getattr(self.driver, '_cdp_event_handlers', None)
        if not isinstance(table, dict):
            return  # Legacy remote-preview path still works without a capture seam.
        for method, callback in (
            ('Network.requestWillBeSent', self._request),
            ('Network.responseReceived', self._response),
            ('Network.loadingFinished', self._finished),
        ):
            previous = table.get(method)

            def handler(message, previous=previous, callback=callback):
                if previous is not None:
                    previous(message)
                callback(message.get('params', {}))

            table[method] = handler
            self.handlers.append((method, previous, handler))

    def close(self):
        table = getattr(self.driver, '_cdp_event_handlers', {})
        for method, previous, handler in self.handlers:
            # Reconnect may have replaced a handler. Never restore a stale one.
            if table.get(method) is handler:
                if previous is None:
                    table.pop(method, None)
                else:
                    table[method] = previous
        self.handlers.clear()

    def _request(self, params):
        request = params.get('request', {})
        url = urlsplit(request.get('url', ''))
        if (request.get('method') != 'POST' or url.scheme != 'https'
                or url.netloc != 'chatgpt.com' or url.path != '/backend-api/files/process_upload_stream'):
            return
        data = request.get('postData', '')
        if not isinstance(data, str) or len(data) > 65536:
            return
        try:
            body = json.loads(data)
        except ValueError:
            return
        if not isinstance(body, dict):
            return
        name, file_id = body.get('file_name'), body.get('file_id')
        if (not isinstance(name, str) or name not in self.names
                or not isinstance(file_id, str) or not file_id or len(file_id) > 256):
            return
        if len(self.requests) >= 32:
            return
        self.bindings.setdefault(name, set()).add(file_id)
        self.requests[params.get('requestId')] = (name, file_id)

    def _response(self, params):
        request_id = params.get('requestId')
        if request_id in self.requests:
            self.statuses[request_id] = params.get('response', {}).get('status', 0)

    def _finished(self, params):
        request_id = params.get('requestId')
        if request_id in self.requests:
            self.finished.add(request_id)

    async def confirmed_names(self):
        if (getattr(self.driver, '_ws', None) is not self.socket
                or getattr(self.driver, '_target_id', None) != self.target):
            raise ImageUploadError('Browser target changed during image upload confirmation')
        if any(len(ids) != 1 for ids in self.bindings.values()):
            raise ImageUploadError('Ambiguous server file IDs for this image upload')
        for request_id in tuple(self.finished - self.checked):
            status = self.statuses.get(request_id, 0)
            if not 200 <= status < 300:
                raise ImageUploadError('Server rejected image processing; no prompt was sent')
            response = await self.driver._cdp(
                'Network.getResponseBody', {'requestId': request_id}, timeout=5, _retry=False)
            result = response.get('result', {})
            wire = result.get('body')
            if response.get('error') or not isinstance(wire, str) or len(wire) > 2 * 1024 * 1024:
                raise ImageUploadError('Image processing response could not be verified')
            if result.get('base64Encoded'):
                try:
                    wire = base64.b64decode(wire, validate=True).decode('utf-8')
                except (ValueError, UnicodeError):
                    raise ImageUploadError('Invalid image processing response') from None
            name, file_id = self.requests[request_id]
            if not upload_completed(wire, file_id):
                raise ImageUploadError('Server did not confirm completed image processing')
            self.confirmed.add(name)
            self.checked.add(request_id)
        return sorted(self.confirmed)
