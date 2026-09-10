"""End-to-end dry-run of main.run() against a sanitized fixture.

No real Qualys credentials or SMTP server are needed: the API call and
SMTP transport are both faked, and state files are written to a tmp
directory rather than the repo.
"""

import json
import smtplib

import pytest

from qualys_tracker import main as main_module


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
    return tmp_path


def _patch_api(monkeypatch, fixture_path):
    with open(fixture_path, encoding="utf-8") as fh:
        payload = json.load(fh)

    def fake_get_portal_version(self):
        return payload

    monkeypatch.setattr(
        main_module.QualysClient, "get_portal_version", fake_get_portal_version
    )


def test_first_run_creates_baseline_without_email(base_env, monkeypatch):
    _patch_api(monkeypatch, "tests/fixtures/valid_response.json")

    exit_code = main_module.run([])

    assert exit_code == 0
    assert (base_env / "tenant_snapshot.json").exists()
    snapshot = json.loads((base_env / "tenant_snapshot.json").read_text())
    assert len(snapshot["modules"]) == 9
    assert not FakeSMTP.sent  # INITIAL_RUN_NOTIFY=false


def test_force_notify_on_first_run_overrides_baseline_suppression(base_env, monkeypatch):
    # A first run with no prior snapshot would normally suppress its email
    # (INITIAL_RUN_NOTIFY=false), but an explicit --force-notify must still
    # send the current inventory rather than being silently swallowed by
    # the baseline path.
    _patch_api(monkeypatch, "tests/fixtures/valid_response.json")

    exit_code = main_module.run(["--force-notify"])

    assert exit_code == 0
    assert len(FakeSMTP.sent) == 1
    assert "Manual / forced notification" in FakeSMTP.sent[0]


def test_second_run_with_no_change_sends_no_email(base_env, monkeypatch):
    _patch_api(monkeypatch, "tests/fixtures/valid_response.json")
    main_module.run([])
    baseline_history = json.loads((base_env / "version_history.json").read_text())
    FakeSMTP.sent = []

    exit_code = main_module.run([])

    assert exit_code == 0
    assert not FakeSMTP.sent
    # No new change -> history is untouched since the baseline run.
    history_after = json.loads((base_env / "version_history.json").read_text())
    assert history_after == baseline_history


def test_third_run_with_change_sends_email_and_records_history(base_env, monkeypatch):
    _patch_api(monkeypatch, "tests/fixtures/valid_response.json")
    main_module.run([])
    FakeSMTP.sent = []

    _patch_api(monkeypatch, "tests/fixtures/valid_response_changed.json")
    exit_code = main_module.run([])

    assert exit_code == 0
    assert len(FakeSMTP.sent) == 1
    assert "FIM" in FakeSMTP.sent[0]

    history = json.loads((base_env / "version_history.json").read_text())
    fim_entries = [h for h in history if h["module"] == "FIM"]
    assert len(fim_entries) == 2  # baseline entry + this change
    latest = fim_entries[-1]
    assert latest["old_version"] == "1.5.1"
    assert latest["new_version"] == "1.5.2"
    assert latest["change_type"] == "VERSION_CHANGED"


def test_idempotent_reprocessing_of_same_response(base_env, monkeypatch):
    _patch_api(monkeypatch, "tests/fixtures/valid_response.json")
    main_module.run([])
    baseline_history = json.loads((base_env / "version_history.json").read_text())
    main_module.run([])
    main_module.run([])
    FakeSMTP.sent = []

    exit_code = main_module.run([])
    assert exit_code == 0
    assert not FakeSMTP.sent
    # Reprocessing the same response over and over never appends more history.
    history_after = json.loads((base_env / "version_history.json").read_text())
    assert history_after == baseline_history


def test_force_notify_sends_even_without_changes(base_env, monkeypatch):
    _patch_api(monkeypatch, "tests/fixtures/valid_response.json")
    main_module.run([])
    FakeSMTP.sent = []

    exit_code = main_module.run(["--force-notify"])

    assert exit_code == 0
    assert len(FakeSMTP.sent) == 1
    assert "Manual / forced notification" in FakeSMTP.sent[0]


def test_reset_baseline_requires_exact_confirmation(base_env, monkeypatch):
    _patch_api(monkeypatch, "tests/fixtures/valid_response.json")
    main_module.run([])

    exit_code = main_module.run(["--reset-baseline", "please"])
    assert exit_code == 2
    assert (base_env / "tenant_snapshot.json").exists()


def test_malformed_response_does_not_overwrite_snapshot(base_env, monkeypatch):
    _patch_api(monkeypatch, "tests/fixtures/valid_response.json")
    main_module.run([])
    original = (base_env / "tenant_snapshot.json").read_text()

    def fake_get_bad(self):
        return {"unexpected": "shape"}

    monkeypatch.setattr(main_module.QualysClient, "get_portal_version", fake_get_bad)
    FakeSMTP.sent = []

    exit_code = main_module.run([])

    assert exit_code == 1
    assert (base_env / "tenant_snapshot.json").read_text() == original
    assert not FakeSMTP.sent


def test_reset_baseline_confirmed_archives_and_recreates(base_env, monkeypatch):
    _patch_api(monkeypatch, "tests/fixtures/valid_response.json")
    main_module.run([])
    FakeSMTP.sent = []

    exit_code = main_module.run(["--reset-baseline", "CONFIRM"])

    assert exit_code == 0
    backups = list(base_env.glob("tenant_snapshot.json.backup-*"))
    assert len(backups) == 1
    snapshot = json.loads((base_env / "tenant_snapshot.json").read_text())
    assert len(snapshot["modules"]) == 9
    # Reset-run is a fresh baseline, so no change email by default.
    assert not FakeSMTP.sent


def test_api_failure_does_not_touch_snapshot_and_fails_job(base_env, monkeypatch):
    _patch_api(monkeypatch, "tests/fixtures/valid_response.json")
    main_module.run([])
    original = (base_env / "tenant_snapshot.json").read_text()
    FakeSMTP.sent = []

    from qualys_tracker.api import QualysAPIError

    def fake_get_error(self):
        raise QualysAPIError("HTTP 503 persisted after 5 attempts")

    monkeypatch.setattr(main_module.QualysClient, "get_portal_version", fake_get_error)

    exit_code = main_module.run([])

    assert exit_code == 1
    assert (base_env / "tenant_snapshot.json").read_text() == original
    assert not FakeSMTP.sent  # no change email; only a staleness alert once past threshold

    run_log = json.loads((base_env / "run_log.json").read_text())
    assert run_log[-1]["api_success"] is False
    assert "503" in run_log[-1]["error"]


def test_staleness_alert_fires_after_threshold_and_is_suppressed_after(base_env, monkeypatch):
    monkeypatch.setenv("STALE_AFTER_DAYS", "3")
    monkeypatch.setenv("STALE_ALERT_SUPPRESSION_DAYS", "3")
    _patch_api(monkeypatch, "tests/fixtures/valid_response.json")
    main_module.run([])  # establishes a successful baseline timestamp

    from qualys_tracker.api import QualysAPIError

    def fake_get_error(self):
        raise QualysAPIError("HTTP 503")

    monkeypatch.setattr(main_module.QualysClient, "get_portal_version", fake_get_error)

    # Manually age the one successful run_log entry so staleness triggers
    # without needing to sleep for real days.
    run_log_path = base_env / "run_log.json"
    run_log = json.loads(run_log_path.read_text())
    run_log[0]["timestamp"] = "2020-01-01T00:00:00Z"
    run_log_path.write_text(json.dumps(run_log))

    FakeSMTP.sent = []
    exit_code = main_module.run([])
    assert exit_code == 1
    assert len(FakeSMTP.sent) == 1
    assert "Staleness Alert" in FakeSMTP.sent[0] or "staleness" in FakeSMTP.sent[0].lower()

    # A second consecutive failure right away must NOT alert again (suppression).
    FakeSMTP.sent = []
    exit_code = main_module.run([])
    assert exit_code == 1
    assert not FakeSMTP.sent


def test_send_test_email_cli_path(base_env, monkeypatch):
    exit_code = main_module.run(["--send-test-email"])
    assert exit_code == 0
    assert len(FakeSMTP.sent) == 1
    assert "Test email" in FakeSMTP.sent[0]
