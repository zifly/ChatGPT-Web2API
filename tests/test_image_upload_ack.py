"""A local thumbnail needs the exact upload's completed server processing event."""
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from chatgpt_web2api.image_input import ImageUploadError
from chatgpt_web2api.image_upload_ack import UploadAcknowledgements, upload_completed


def event(kind='file.processing.completed', file_id='file-test'):
    return json.dumps({'file_id': file_id, 'event': kind})


@pytest.mark.parametrize('wire', [
    event(), 'data: ' + event() + '\n\ndata: [DONE]\n',
    event('file.processing.started') + '\n' + event('file.processing.file_ready') + '\n' + event(),
])
def test_only_completed_processing_is_accepted(wire):
    assert upload_completed(wire, 'file-test')


@pytest.mark.parametrize('wire', [
    '', event('file.processing.started'), event('file.processing.file_ready'),
    event(file_id='other-file'), 'malformed',
    event() + '\n' + event('file.processing.failed'),
    json.dumps({'file_id': 'file-test', 'event': 'file.processing.completed', 'error': 'failed'}),
])
def test_partial_unrelated_or_failed_upload_is_not_confirmed(wire):
    assert not upload_completed(wire, 'file-test')


def capture(driver, method, params):
    driver._cdp_event_handlers[method]({'params': params})


def request(driver, *, name='owned.png', file_id='file-test', rid='request-1',
            url='https://chatgpt.com/backend-api/files/process_upload_stream'):
    capture(driver, 'Network.requestWillBeSent', {'requestId': rid, 'request': {
        'url': url, 'method': 'POST', 'postData': json.dumps({'file_name': name, 'file_id': file_id})}})


def response(driver, *, rid='request-1', status=200, finished=True):
    capture(driver, 'Network.responseReceived', {'requestId': rid, 'response': {'status': status}})
    if finished:
        capture(driver, 'Network.loadingFinished', {'requestId': rid})


def driver(wire=None):
    prior = Mock()
    d = SimpleNamespace(_ws=object(), _target_id='owned-target',
                        _cdp_event_handlers={'Network.requestWillBeSent': prior},
                        _cdp=AsyncMock(return_value={'result': {'body': event() if wire is None else wire}}))
    return d, prior


@pytest.mark.parametrize('encoded', [False, True])
async def test_filename_id_and_finished_response_are_all_required(encoded):
    d, prior = driver()
    if encoded:
        d._cdp.return_value = {'result': {'body': base64.b64encode(event().encode()).decode(),
                                         'base64Encoded': True}}
    scope = UploadAcknowledgements(d, ['owned.png'])
    scope.attach()
    request(d)
    response(d, finished=False)
    assert await scope.confirmed_names() == []
    d._cdp.assert_not_awaited()
    capture(d, 'Network.loadingFinished', {'requestId': 'request-1'})
    assert await scope.confirmed_names() == ['owned.png']
    assert await scope.confirmed_names() == ['owned.png']
    d._cdp.assert_awaited_once()
    assert d._cdp.await_args.kwargs['_retry'] is False
    prior.assert_called_once()
    scope.close()
    assert d._cdp_event_handlers == {'Network.requestWillBeSent': prior}


@pytest.mark.parametrize('changes', [
    {'name': 'foreign.png'},
    {'url': 'https://unrelated.example/backend-api/files/process_upload_stream'},
])
async def test_unrelated_uploads_cannot_confirm_a_thumbnail(changes):
    d, _ = driver()
    scope = UploadAcknowledgements(d, ['owned.png'])
    scope.attach()
    request(d, **changes)
    response(d)
    assert await scope.confirmed_names() == []
    d._cdp.assert_not_awaited()
    scope.close()


@pytest.mark.parametrize('status,wire', [(429, event()), (200, event('file.processing.file_ready'))])
async def test_http_success_alone_or_error_status_cannot_confirm(status, wire):
    d, _ = driver(wire)
    scope = UploadAcknowledgements(d, ['owned.png'])
    scope.attach()
    request(d)
    response(d, status=status)
    with pytest.raises(ImageUploadError):
        await scope.confirmed_names()
    scope.close()


async def test_reconnect_invalidates_capture_without_clobbering_new_handler():
    d, _ = driver()
    scope = UploadAcknowledgements(d, ['owned.png'])
    scope.attach()
    request(d)
    replacement = Mock()
    d._ws = object()
    d._cdp_event_handlers['Network.requestWillBeSent'] = replacement
    with pytest.raises(ImageUploadError, match='target changed'):
        await scope.confirmed_names()
    scope.close()
    assert d._cdp_event_handlers['Network.requestWillBeSent'] is replacement


async def test_conflicting_file_ids_are_rejected():
    d, _ = driver()
    scope = UploadAcknowledgements(d, ['owned.png'])
    scope.attach()
    request(d)
    request(d, file_id='different-file', rid='request-2')
    with pytest.raises(ImageUploadError, match='Ambiguous'):
        await scope.confirmed_names()
    scope.close()
