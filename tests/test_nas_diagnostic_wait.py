import importlib.util
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest


@pytest.fixture
def diagnostic(monkeypatch):
    spec = importlib.util.spec_from_file_location('nas_diagnostic', Path(__file__).parents[1] / 'scripts/check-nas-api.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    clock = [0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    return module


def test_waits_through_closed_port_and_browser_startup(diagnostic):
    ready = {'chrome_running': True, 'driver_connected': True}
    request = Mock(side_effect=[URLError('connection refused'), {'chrome_running': True, 'driver_connected': False}, ready])
    assert diagnostic.wait_for_health(request, 10) == ready
    assert request.call_count == 3
    assert all(call.args == ('/health',) for call in request.call_args_list)


def test_no_wait_keeps_immediate_failure(diagnostic):
    request = Mock(side_effect=URLError('connection refused'))
    with pytest.raises(URLError):
        diagnostic.wait_for_health(request, 0)
    assert request.call_count == 1


def test_wait_has_deadline(diagnostic):
    request = Mock(side_effect=URLError('connection refused'))
    with pytest.raises(URLError):
        diagnostic.wait_for_health(request, 3)
    assert request.call_count == 3


def test_does_not_retry_authentication_failures(diagnostic):
    request = Mock(side_effect=HTTPError('http://example.invalid/health', 401, 'Unauthorized', None, None))
    with pytest.raises(HTTPError):
        diagnostic.wait_for_health(request, 120)
    assert request.call_count == 1


def test_open_breaker_is_reported_immediately(diagnostic):
    health = {'chrome_running': False, 'open_breakers': ['auth_expired']}
    request = Mock(return_value=health)
    assert diagnostic.wait_for_health(request, 120) == health
    assert request.call_count == 1
