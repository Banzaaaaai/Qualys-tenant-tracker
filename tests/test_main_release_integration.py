"""End-to-end tests of main.run() with release-notes correlation enabled.

Unlike test_main_integration.py (which disables CHECK_PUBLIC_RELEASES to
stay focused on tenant tracking), these tests exercise the full wiring:
tenant API -> comparison -> release-notes correlation -> enhanced email,
plus the standalone "Qualys announced something, tenant unchanged" path.
All HTTP (both the Qualys tenant API and the public release-notes site)
is mocked -- no live network, no real credentials.
"""

import json
import os
import smtplib

import pytest
import requests

from qualys_tracker import main as main_module
from qualys_tracker import release_notes as release_notes_module

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
RELEASE_FIXTURES_DIR = os.path.join(FIXTURES_DIR, "release_notes")


def _read_release_fixture(name: str) -> str:
    with open(os.path.join(RELEASE_FIXTURES_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def _read_tenant_fixture(name: str) -> dict:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, username, password):
        pass

    def sendmail(self, from_addr, to_addrs, message):
        FakeSMTP.sent.append(message)


@pytest.fixture(autouse=True)
def patch_smtp(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    # QualysReleaseNotesClient binds `sleep_fn=time.sleep` as a default
    # argument at class-definition time, so patching time.sleep after
    # the fact has no effect on already-defined defaults. Patch the
    # constructor as main.py sees it instead, forcing a no-op sleep so
    # a simulated retry/backoff scenario doesn't cost real wall-clock
    # seconds in the test suite.
    original = release_notes_module.QualysReleaseNotesClient

    def fast_client(*args, **kwargs):
        kwargs.setdefault("sleep_fn", lambda seconds: None)
        return original(*args, **kwargs)

    monkeypatch.setattr(main_module, "QualysReleaseNotesClient", fast_client)


@pytest.fixture
def base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("QUALYS_API_URL", "https://qualysapi.example.com")
    monkeypatch.setenv("QUALYS_USERNAME", "svc-account")
    monkeypatch.setenv("QUALYS_PASSWORD", "not-a-real-secret")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("EMAIL_FROM", "tracker@example.com")
    monkeypatch.setenv("EMAIL_TO", "ops@example.com")
    monkeypatch.setenv("TRACKER_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TRACKER_SCHEDULE_GUARD_ENABLED", "false")
    monkeypatch.setenv("INITIAL_RUN_NOTIFY", "false")
    monkeypatch.setenv("RELEASE_NOTES_CACHE_TTL_DAYS", "1")
    return tmp_path


def _patch_tenant_api(monkeypatch, fixture_name: str):
    payload = _read_tenant_fixture(fixture_name)

    def fake_get_portal_version(self):
        return payload

    monkeypatch.setattr(main_module.QualysClient, "get_portal_version", fake_get_portal_version)


class _FakeHTTPResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self.ok = 200 <= status_code < 300


def _patch_release_site(monkeypatch, available=True):
    """FIM index has two versions: 4.9.3 (old) and 4.9.4 (latest). Every
    other module's product name has no hint/match, so it naturally comes
    back "release notes: not found" without special-casing it here."""
    index_html = _read_release_fixture("index_sample.html")
    pages = {
        "release-notes": index_html,
        "release_4_9_4.htm": _read_release_fixture("fim_4_9_4_release.html"),
        "release_4_9_3.htm": _read_release_fixture("fim_4_9_3_release.html"),
        "release_6_0_1.htm": _read_release_fixture("no_features_release.html"),
    }

    def fake_get(url, headers=None, timeout=None):
        if not available:
            return _FakeHTTPResponse(503)
        for suffix, content in pages.items():
            if url.endswith(suffix):
                return _FakeHTTPResponse(200, content)
        return _FakeHTTPResponse(404, "")

    monkeypatch.setattr(requests, "get", fake_get)


def test_tenant_change_with_no_newer_public_version(base_env, monkeypatch):
    """FIM changes 4.9.3 -> 4.9.4 on the tenant, and 4.9.4 IS the latest
    public release too -- status should be CURRENT, not a false alarm."""
    # Isolate the tenant-change correlation path from the standalone scan
    # (covered separately below) -- the sample index also happens to list
    # a newer WAS release, which would otherwise add an unrelated email.
    monkeypatch.setenv("CHECK_PUBLIC_RELEASES", "false")
    _patch_tenant_api(monkeypatch, "valid_response.json")
    main_module.run([])  # baseline (FIM=1.5.1 per the base fixture -- irrelevant here)

    # Second run: only FIM changes, to a version the release site knows about.
    payload = _read_tenant_fixture("valid_response.json")
    payload["ServiceResponse"]["data"][0]["Portal-Version"]["FIM-VERSION"] = "4.9.4"

    def fake_get_portal_version(self):
        return payload

    monkeypatch.setattr(main_module.QualysClient, "get_portal_version", fake_get_portal_version)
    _patch_release_site(monkeypatch)

    exit_code = main_module.run([])

    assert exit_code == 0
    assert len(FakeSMTP.sent) == 1
    message = FakeSMTP.sent[0]
    assert "FIM upgraded" in message
    assert "CURRENT" in message
    assert "already running the latest publicly announced version" in message

    run_log = json.loads((base_env / "run_log.json").read_text())
    assert run_log[-1]["release_notes_lookup_success"] is True


def test_tenant_change_with_newer_public_version_available(base_env, monkeypatch):
    """FIM changes to 4.9.3 on the tenant, but the site's latest is 4.9.4."""
    monkeypatch.setenv("CHECK_PUBLIC_RELEASES", "false")
    _patch_tenant_api(monkeypatch, "valid_response.json")
    main_module.run([])

    payload = _read_tenant_fixture("valid_response.json")
    payload["ServiceResponse"]["data"][0]["Portal-Version"]["FIM-VERSION"] = "4.9.3"

    def fake_get_portal_version(self):
        return payload

    monkeypatch.setattr(main_module.QualysClient, "get_portal_version", fake_get_portal_version)
    _patch_release_site(monkeypatch)

    exit_code = main_module.run([])

    assert exit_code == 0
    message = FakeSMTP.sent[0]
    assert "NOT YET DETECTED ON THIS TENANT" in message
    assert "Create Incident for Individual Events" in message  # a real 4.9.4 feature


def test_release_note_lookup_failure_does_not_block_tenant_tracking(base_env, monkeypatch):
    monkeypatch.setenv("CHECK_PUBLIC_RELEASES", "false")
    _patch_tenant_api(monkeypatch, "valid_response.json")
    main_module.run([])

    payload = _read_tenant_fixture("valid_response.json")
    payload["ServiceResponse"]["data"][0]["Portal-Version"]["FIM-VERSION"] = "4.9.4"

    def fake_get_portal_version(self):
        return payload

    monkeypatch.setattr(main_module.QualysClient, "get_portal_version", fake_get_portal_version)
    _patch_release_site(monkeypatch, available=False)  # release-notes site is down

    exit_code = main_module.run([])

    # The tenant version change must still be tracked and emailed...
    assert exit_code == 0
    snapshot = json.loads((base_env / "tenant_snapshot.json").read_text())
    assert snapshot["modules"]["FIM"]["version"] == "4.9.4"
    assert len(FakeSMTP.sent) == 1
    assert "FIM upgraded" in FakeSMTP.sent[0]
    assert "Temporarily unavailable" in FakeSMTP.sent[0]

    # ...and the failure is visible in the run log, separately from API success.
    run_log = json.loads((base_env / "run_log.json").read_text())
    assert run_log[-1]["api_success"] is True
    assert run_log[-1]["release_notes_lookup_success"] is False


def test_standalone_public_release_announcement_when_tenant_unchanged(base_env, monkeypatch):
    """FIM stays at 4.9.3 on the tenant across two runs; Qualys's site
    already lists 4.9.4 as available. No tenant change occurs, but the
    tracker should still tell the user about the announcement once."""
    payload = _read_tenant_fixture("valid_response.json")
    payload["ServiceResponse"]["data"][0]["Portal-Version"]["FIM-VERSION"] = "4.9.3"

    def fake_get_portal_version(self):
        return payload

    monkeypatch.setattr(main_module.QualysClient, "get_portal_version", fake_get_portal_version)
    main_module.run([])  # baseline, FIM=4.9.3

    _patch_release_site(monkeypatch)
    exit_code = main_module.run([])

    assert exit_code == 0
    assert len(FakeSMTP.sent) == 1
    assert "Qualys Public Release Announcement" in FakeSMTP.sent[0]
    assert "4.9.4" in FakeSMTP.sent[0]


def test_standalone_public_release_announcement_is_not_repeated(base_env, monkeypatch):
    payload = _read_tenant_fixture("valid_response.json")
    payload["ServiceResponse"]["data"][0]["Portal-Version"]["FIM-VERSION"] = "4.9.3"

    def fake_get_portal_version(self):
        return payload

    monkeypatch.setattr(main_module.QualysClient, "get_portal_version", fake_get_portal_version)
    main_module.run([])  # baseline

    _patch_release_site(monkeypatch)
    main_module.run([])  # first announcement -- sent
    FakeSMTP.sent = []

    exit_code = main_module.run([])  # nothing changed since -- must not resend
    assert exit_code == 0
    assert not FakeSMTP.sent


def test_public_release_notification_disabled_suppresses_standalone_email(base_env, monkeypatch):
    monkeypatch.setenv("PUBLIC_RELEASE_NOTIFICATION", "false")
    payload = _read_tenant_fixture("valid_response.json")
    payload["ServiceResponse"]["data"][0]["Portal-Version"]["FIM-VERSION"] = "4.9.3"

    def fake_get_portal_version(self):
        return payload

    monkeypatch.setattr(main_module.QualysClient, "get_portal_version", fake_get_portal_version)
    main_module.run([])

    _patch_release_site(monkeypatch)
    exit_code = main_module.run([])
    assert exit_code == 0
    assert not FakeSMTP.sent
