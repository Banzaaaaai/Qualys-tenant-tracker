import json
import os

import pytest

from qualys_tracker import history, state
from qualys_tracker.comparison import compare
from qualys_tracker.models import ModuleVersion


def test_load_snapshot_missing_returns_none(tmp_path):
    path = str(tmp_path / "tenant_snapshot.json")
    assert state.load_snapshot(path) is None


def test_load_snapshot_valid(tmp_path):
    path = tmp_path / "tenant_snapshot.json"
    payload = {"tenant": {}, "last_checked": "x", "modules": {"FIM": {"version": "1.0"}}}
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = state.load_snapshot(str(path))
    assert loaded["modules"]["FIM"]["version"] == "1.0"


def test_load_snapshot_corrupted_json_raises(tmp_path):
    path = tmp_path / "tenant_snapshot.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(state.StateCorruptionError):
        state.load_snapshot(str(path))


def test_load_snapshot_wrong_shape_raises(tmp_path):
    path = tmp_path / "tenant_snapshot.json"
    path.write_text(json.dumps({"unexpected": True}), encoding="utf-8")
    with pytest.raises(state.StateCorruptionError):
        state.load_snapshot(str(path))


def test_atomic_update_produces_valid_file_and_no_tmp_leftovers(tmp_path):
    path = str(tmp_path / "tenant_snapshot.json")
    state.save_snapshot_atomic(path, {"tenant": {}, "last_checked": "x", "modules": {}})
    assert os.path.exists(path)
    remaining = os.listdir(tmp_path)
    assert remaining == ["tenant_snapshot.json"]

    with open(path, encoding="utf-8") as fh:
        assert json.load(fh)["modules"] == {}


def test_build_snapshot_first_run_stamps_first_seen_and_last_changed():
    current = [ModuleVersion("FIM-VERSION", "FIM", "1.0.0")]
    result = compare({}, current)
    snapshot = state.build_snapshot(
        None, result, {"FIM": current[0]}, "2026-01-01T00:00:00Z",
        "https://example.com", "tenant-1",
    )
    assert snapshot["modules"]["FIM"]["first_seen"] == "2026-01-01T00:00:00Z"
    assert snapshot["modules"]["FIM"]["last_changed"] == "2026-01-01T00:00:00Z"


def test_build_snapshot_unchanged_module_keeps_timestamps():
    previous = {
        "tenant": {},
        "last_checked": "old",
        "modules": {
            "FIM": {"version": "1.0.0", "api_field": "FIM-VERSION",
                     "first_seen": "2025-01-01T00:00:00Z", "last_changed": "2025-06-01T00:00:00Z"}
        },
    }
    current = [ModuleVersion("FIM-VERSION", "FIM", "1.0.0")]
    result = compare(previous["modules"], current)
    snapshot = state.build_snapshot(
        previous, result, {"FIM": current[0]}, "2026-01-01T00:00:00Z",
        "https://example.com", "tenant-1",
    )
    assert snapshot["modules"]["FIM"]["first_seen"] == "2025-01-01T00:00:00Z"
    assert snapshot["modules"]["FIM"]["last_changed"] == "2025-06-01T00:00:00Z"


def test_build_snapshot_changed_module_bumps_last_changed_only():
    previous = {
        "modules": {
            "FIM": {"version": "1.0.0", "api_field": "FIM-VERSION",
                     "first_seen": "2025-01-01T00:00:00Z", "last_changed": "2025-06-01T00:00:00Z"}
        }
    }
    current = [ModuleVersion("FIM-VERSION", "FIM", "1.0.1")]
    result = compare(previous["modules"], current)
    snapshot = state.build_snapshot(
        previous, result, {"FIM": current[0]}, "2026-01-01T00:00:00Z",
        "https://example.com", "tenant-1",
    )
    assert snapshot["modules"]["FIM"]["first_seen"] == "2025-01-01T00:00:00Z"
    assert snapshot["modules"]["FIM"]["last_changed"] == "2026-01-01T00:00:00Z"
    assert snapshot["modules"]["FIM"]["version"] == "1.0.1"


def test_build_snapshot_drops_removed_modules():
    previous = {"modules": {"WAF": {"version": "1.0", "api_field": "WAF-VERSION",
                                     "first_seen": "x", "last_changed": "x"}}}
    result = compare(previous["modules"], [])
    snapshot = state.build_snapshot(
        previous, result, {}, "2026-01-01T00:00:00Z", "https://example.com", "tenant-1",
    )
    assert "WAF" not in snapshot["modules"]


def test_history_append_only_on_change(tmp_path):
    path = str(tmp_path / "version_history.json")
    current = [ModuleVersion("FIM-VERSION", "FIM", "1.0.0")]
    result = compare({"FIM": {"version": "1.0.0"}}, current)  # unchanged
    history.append_history_entries(path, result.all_changes, "2026-01-01T00:00:00Z")
    assert not os.path.exists(path)


def test_history_duplicate_prevention_across_unchanged_runs(tmp_path):
    path = str(tmp_path / "version_history.json")
    current = [ModuleVersion("FIM-VERSION", "FIM", "1.0.1")]
    result = compare({"FIM": {"version": "1.0.0"}}, current)
    history.append_history_entries(path, result.all_changes, "2026-01-01T00:00:00Z")

    unchanged_result = compare({"FIM": {"version": "1.0.1"}}, current)
    history.append_history_entries(path, unchanged_result.all_changes, "2026-01-02T00:00:00Z")

    entries = history.load_history(path)
    assert len(entries) == 1
