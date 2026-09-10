import requests

from qualys_tracker.api import (
    QualysAPIError,
    QualysAuthError,
    QualysClient,
    QualysRetryExhaustedError,
    QualysTimeoutError,
)


class FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None):
        self.status_code = status_code
        self._json_data = json_data
        self.headers = headers or {}
        self.ok = 200 <= status_code < 300

    def json(self):
        if self._json_data is None:
            raise ValueError("no json")
        return self._json_data


def make_client(monkeypatch, responses, max_retries=5):
    calls = {"count": 0}

    def fake_get(url, headers=None, auth=None, timeout=None):
        item = responses[min(calls["count"], len(responses) - 1)]
        calls["count"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(requests, "get", fake_get)
    client = QualysClient(
        "https://qualysapi.example.com", "user", "pass",
        max_retries=max_retries, sleep_fn=lambda seconds: None,
    )
    return client, calls


def test_200_returns_json(monkeypatch):
    client, calls = make_client(monkeypatch, [FakeResponse(200, {"ok": True})])
    result = client.get_portal_version()
    assert result == {"ok": True}
    assert calls["count"] == 1


def test_401_raises_auth_error_without_retry(monkeypatch):
    client, calls = make_client(monkeypatch, [FakeResponse(401)])
    try:
        client.get_portal_version()
        assert False, "expected QualysAuthError"
    except QualysAuthError:
        pass
    assert calls["count"] == 1  # no retries for auth failures


def test_403_raises_auth_error_without_retry(monkeypatch):
    client, calls = make_client(monkeypatch, [FakeResponse(403)])
    try:
        client.get_portal_version()
        assert False, "expected QualysAuthError"
    except QualysAuthError:
        pass
    assert calls["count"] == 1


def test_429_retries_then_succeeds(monkeypatch):
    client, calls = make_client(
        monkeypatch,
        [FakeResponse(429), FakeResponse(429), FakeResponse(200, {"ok": True})],
    )
    result = client.get_portal_version()
    assert result == {"ok": True}
    assert calls["count"] == 3


def test_500_retries_then_succeeds(monkeypatch):
    client, calls = make_client(
        monkeypatch, [FakeResponse(500), FakeResponse(200, {"ok": True})]
    )
    result = client.get_portal_version()
    assert result == {"ok": True}
    assert calls["count"] == 2


def test_retry_exhaustion_raises(monkeypatch):
    client, calls = make_client(
        monkeypatch, [FakeResponse(503)] * 3, max_retries=3
    )
    try:
        client.get_portal_version()
        assert False, "expected QualysRetryExhaustedError"
    except QualysRetryExhaustedError:
        pass
    assert calls["count"] == 3


def test_timeout_retries_then_raises(monkeypatch):
    client, calls = make_client(
        monkeypatch, [requests.Timeout("timed out")] * 3, max_retries=3
    )
    try:
        client.get_portal_version()
        assert False, "expected QualysTimeoutError"
    except QualysTimeoutError:
        pass
    assert calls["count"] == 3


def test_timeout_then_recovers(monkeypatch):
    client, calls = make_client(
        monkeypatch, [requests.Timeout("timed out"), FakeResponse(200, {"ok": True})]
    )
    result = client.get_portal_version()
    assert result == {"ok": True}
    assert calls["count"] == 2


def test_unexpected_status_raises_api_error(monkeypatch):
    client, calls = make_client(monkeypatch, [FakeResponse(418)])
    try:
        client.get_portal_version()
        assert False, "expected QualysAPIError"
    except QualysAPIError:
        pass
