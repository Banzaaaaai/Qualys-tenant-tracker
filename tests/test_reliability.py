import json
from datetime import datetime, timezone

import pytest
import requests

from qualys_tracker import persistence, history, state
from qualys_tracker.config import ConfigError, QualysConfig, TrackerConfig, ReleaseNotesConfig
from qualys_tracker.heartbeat import ping_heartbeat
from qualys_tracker.release_notes import QualysReleaseNotesClient, ReleaseNotesError
from qualys_tracker.scheduling import is_within_scheduled_window


def test_transaction_recovers_after_partial_write(tmp_path, monkeypatch):
    original = persistence.save_snapshot_atomic
    updates = {"tenant_snapshot.json": {"modules": {}}, "version_history.json": [{"module": "FIM"}], "notification_outbox.json": [{"id": "event"}]}
    def interrupted(path, value):
        if str(path).endswith("version_history.json"):
            raise OSError("simulated interruption")
        original(path, value)
    monkeypatch.setattr(persistence, "save_snapshot_atomic", interrupted)
    with pytest.raises(OSError):
        persistence.commit(str(tmp_path), updates)
    assert (tmp_path / persistence.JOURNAL).exists()
    monkeypatch.setattr(persistence, "save_snapshot_atomic", original)
    persistence.recover(str(tmp_path))
    persistence.recover(str(tmp_path))
    assert not (tmp_path / persistence.JOURNAL).exists()
    for name, expected in updates.items():
        assert json.loads((tmp_path / name).read_text()) == expected


def test_corrupt_history_is_preserved(tmp_path):
    path = tmp_path / "version_history.json"
    path.write_text("broken")
    with pytest.raises(ValueError, match="preserved"):
        history.load_history(str(path))
    assert path.read_text() == "broken"


@pytest.mark.parametrize("module", [None, {}, {"version": 42}, {"version": ""}, {"version": "1", "first_seen": []}])
def test_invalid_module_records_are_rejected(tmp_path, module):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"modules": {"FIM": module}}))
    with pytest.raises(state.StateCorruptionError):
        state.load_snapshot(str(path))


@pytest.mark.parametrize("name,value", [("QUALYS_HTTP_TIMEOUT_SECONDS", "0"), ("QUALYS_HTTP_MAX_RETRIES", "-1")])
def test_invalid_api_ranges(monkeypatch, name, value):
    for key, val in {"QUALYS_API_URL": "https://example.com", "QUALYS_USERNAME": "user", "QUALYS_PASSWORD": "pass", name: value}.items():
        monkeypatch.setenv(key, val)
    with pytest.raises(ConfigError):
        QualysConfig.from_env()


@pytest.mark.parametrize("name,value", [("TRACKER_TIMEZONE", "Unknown/Timezone"), ("TRACKER_LOCAL_RUN_TIMES", "25:00"), ("TRACKER_LOCAL_RUN_TIMES", ","), ("INITIAL_RUN_NOTIFY", "maybe"), ("RUN_LOG_MAX_ENTRIES", "0")])
def test_invalid_tracker_config(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        TrackerConfig.from_env("https://example.com")


def test_invalid_release_budget(monkeypatch):
    monkeypatch.setenv("RELEASE_NOTES_BUDGET_SECONDS", "-1")
    with pytest.raises(ConfigError):
        ReleaseNotesConfig.from_env()


def test_winter_early_trigger_skipped():
    now = datetime(2026, 1, 15, 6, 45, tzinfo=timezone.utc)
    assert not is_within_scheduled_window(now, "Europe/Amsterdam", ["08:45"], 90).should_run


def test_failed_index_fetch_is_attempted_only_once_per_run(monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise requests.Timeout()
    monkeypatch.setattr(requests, "get", fail)
    client = QualysReleaseNotesClient(max_retries=2, sleep_fn=lambda _: None)
    for _ in range(10):
        with pytest.raises(ReleaseNotesError):
            client.fetch_index_entries()
    assert len(calls) == 2


def test_lookup_budget_stops_more_network_calls(monkeypatch):
    from qualys_tracker import release_notes
    ticks = [100.0]
    monkeypatch.setattr(release_notes.time, "monotonic", lambda: ticks[0])
    client = QualysReleaseNotesClient()
    client.set_budget(5)
    ticks[0] = 106
    def forbidden(*args, **kwargs):
        pytest.fail("budget exhausted before request")
    monkeypatch.setattr(requests, "get", forbidden)
    with pytest.raises(ReleaseNotesError, match="budget"):
        client.fetch_index_entries()


def test_heartbeat_failure_does_not_leak_token(monkeypatch, capsys):
    monkeypatch.setenv("TRACKER_HEARTBEAT_URL", "https://monitor.example/secret-token")
    def fail(*args, **kwargs):
        raise requests.Timeout("secret-token")
    monkeypatch.setattr(requests, "get", fail)
    assert ping_heartbeat() is False
    assert "secret-token" not in capsys.readouterr().out


def test_unconfigured_heartbeat_does_not_contact_network(monkeypatch):
    monkeypatch.delenv("TRACKER_HEARTBEAT_URL", raising=False)
    monkeypatch.setattr(requests, "get", lambda *a, **kw: pytest.fail("unexpected network"))
    assert ping_heartbeat() is None


def test_state_directory_rejects_concurrent_writer(tmp_path):
    from qualys_tracker.locking import state_lock
    with state_lock(str(tmp_path)):
        with pytest.raises(RuntimeError, match="Another tracker"):
            with state_lock(str(tmp_path)):
                pass
    with state_lock(str(tmp_path)):
        pass
