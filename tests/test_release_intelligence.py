import os

import pytest
import requests

from qualys_tracker import release_cache, release_intelligence
from qualys_tracker.models import UpgradeStatus
from qualys_tracker.release_notes import QualysReleaseNotesClient

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "release_notes")


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as fh:
        return fh.read()


class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self.ok = 200 <= status_code < 300


@pytest.fixture
def fake_site(monkeypatch):
    """Routes GETs to fixture files keyed by URL suffix."""
    pages = {
        "release-notes": _read("index_sample.html"),
        "release_4_9_4.htm": _read("fim_4_9_4_release.html"),
        "release_4_9_3.htm": _read("fim_4_9_3_release.html"),
        "release_6_0_1.htm": _read("no_features_release.html"),
    }

    def fake_get(url, headers=None, timeout=None):
        for suffix, content in pages.items():
            if url.endswith(suffix):
                return _FakeResponse(200, content)
        return _FakeResponse(404, "")

    monkeypatch.setattr(requests, "get", fake_get)
    return pages


NOW = "2026-09-10T08:00:00Z"


def test_resolve_tenant_release_exact_match(fake_site):
    client = QualysReleaseNotesClient()
    cache = release_cache.load_cache("/nonexistent")
    release = release_intelligence.resolve_tenant_release(client, cache, "FIM", "4.9.3", NOW)
    assert release is not None
    assert release.version == "4.9.3"
    assert release.release_date == "August 12, 2026"


def test_resolve_tenant_release_not_found_returns_none_not_error(fake_site):
    client = QualysReleaseNotesClient()
    cache = release_cache.load_cache("/nonexistent")
    release = release_intelligence.resolve_tenant_release(client, cache, "FIM", "9.9.9", NOW)
    assert release is None


def test_resolve_tenant_release_uses_cache_without_refetching(fake_site, monkeypatch):
    client = QualysReleaseNotesClient()
    cache = release_cache.load_cache("/nonexistent")
    release_intelligence.resolve_tenant_release(client, cache, "FIM", "4.9.4", NOW)

    call_count = {"n": 0}
    original = requests.get

    def counting_get(*a, **k):
        call_count["n"] += 1
        return original(*a, **k)

    monkeypatch.setattr(requests, "get", counting_get)
    release_intelligence.resolve_tenant_release(client, cache, "FIM", "4.9.4", NOW)
    assert call_count["n"] == 0  # served entirely from cache


def test_resolve_latest_public_picks_newest(fake_site):
    client = QualysReleaseNotesClient()
    cache = release_cache.load_cache("/nonexistent")
    release, confident = release_intelligence.resolve_latest_public(client, cache, "FIM", 1, NOW)
    assert confident
    assert release.version == "4.9.4"


def test_resolve_latest_public_respects_cache_ttl(fake_site, monkeypatch, tmp_path):
    cache_path = str(tmp_path / "cache.json")
    client = QualysReleaseNotesClient()
    cache = release_cache.load_cache(cache_path)
    release_intelligence.resolve_latest_public(client, cache, "FIM", 1, NOW)
    release_cache.save_cache(cache_path, cache)

    reloaded = release_cache.load_cache(cache_path)
    call_count = {"n": 0}
    original = requests.get

    def counting_get(*a, **k):
        call_count["n"] += 1
        return original(*a, **k)

    monkeypatch.setattr(requests, "get", counting_get)
    client2 = QualysReleaseNotesClient()
    release_intelligence.resolve_latest_public(client2, reloaded, "FIM", 1, "2026-09-10T09:00:00Z")
    assert call_count["n"] == 0  # within TTL, no re-fetch


def test_resolve_latest_public_expired_cache_refreshes(fake_site, tmp_path):
    cache_path = str(tmp_path / "cache.json")
    client = QualysReleaseNotesClient()
    cache = release_cache.load_cache(cache_path)
    release_intelligence.resolve_latest_public(client, cache, "FIM", 1, "2026-09-01T08:00:00Z")
    release_cache.save_cache(cache_path, cache)

    reloaded = release_cache.load_cache(cache_path)
    client2 = QualysReleaseNotesClient()
    # 9 days later -- well past a 1-day TTL -- must refresh, not silently
    # serve the stale cached value forever.
    release, confident = release_intelligence.resolve_latest_public(
        client2, reloaded, "FIM", 1, "2026-09-10T08:00:00Z"
    )
    assert confident
    assert release.version == "4.9.4"


def test_failed_refresh_keeps_prior_cache_entry_on_disk(fake_site, tmp_path, monkeypatch):
    cache_path = str(tmp_path / "cache.json")
    client = QualysReleaseNotesClient()
    cache = release_cache.load_cache(cache_path)
    release_intelligence.resolve_latest_public(client, cache, "FIM", 1, "2026-09-01T08:00:00Z")
    release_cache.save_cache(cache_path, cache)
    before = release_cache.load_cache(cache_path)

    def failing_get(*a, **k):
        return _FakeResponse(503)

    monkeypatch.setattr(requests, "get", failing_get)
    client2 = QualysReleaseNotesClient(max_retries=1, sleep_fn=lambda s: None)
    reloaded = release_cache.load_cache(cache_path)
    intel = release_intelligence.build_module_intelligence(
        client2, reloaded, "FIM", "4.9.3", 1, "2026-09-10T08:00:00Z"
    )
    assert intel.upgrade_status == UpgradeStatus.RELEASE_NOTE_LOOKUP_FAILED
    # We never wrote the failed attempt back over the good cache file.
    still_there = release_cache.load_cache(cache_path)
    assert still_there == before


def test_build_module_intelligence_current(fake_site):
    client = QualysReleaseNotesClient()
    cache = release_cache.load_cache("/nonexistent")
    intel = release_intelligence.build_module_intelligence(client, cache, "FIM", "4.9.4", 1, NOW)
    assert intel.upgrade_status == UpgradeStatus.CURRENT
    assert intel.tenant_release.version == "4.9.4"
    assert intel.latest_public_release.version == "4.9.4"


def test_build_module_intelligence_newer_public_available(fake_site):
    client = QualysReleaseNotesClient()
    cache = release_cache.load_cache("/nonexistent")
    intel = release_intelligence.build_module_intelligence(client, cache, "FIM", "4.9.3", 1, NOW)
    assert intel.upgrade_status == UpgradeStatus.PUBLIC_NEWER_VERSION_AVAILABLE
    assert intel.tenant_release.version == "4.9.3"
    assert intel.latest_public_release.version == "4.9.4"


def test_build_module_intelligence_release_not_found_for_unknown_module(fake_site):
    client = QualysReleaseNotesClient()
    cache = release_cache.load_cache("/nonexistent")
    intel = release_intelligence.build_module_intelligence(client, cache, "ZZZ", "1.0.0", 1, NOW)
    assert intel.tenant_release is None
    assert intel.upgrade_status == UpgradeStatus.PUBLIC_RELEASE_NOT_FOUND


def test_build_module_intelligence_lookup_failure_does_not_raise(monkeypatch):
    def failing_get(*a, **k):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(requests, "get", failing_get)
    client = QualysReleaseNotesClient(max_retries=1, sleep_fn=lambda s: None)
    cache = release_cache.load_cache("/nonexistent")
    intel = release_intelligence.build_module_intelligence(client, cache, "FIM", "4.9.3", 1, NOW)
    assert intel.upgrade_status == UpgradeStatus.RELEASE_NOTE_LOOKUP_FAILED
    assert intel.error is not None


# --- Standalone public-release announcement (no tenant change) --------


def test_check_public_release_announcements_detects_new_version(fake_site):
    client = QualysReleaseNotesClient()
    cache = release_cache.load_cache("/nonexistent")
    modules = {"FIM": {"version": "4.9.3"}}
    results = release_intelligence.check_public_release_announcements(
        client, cache, {}, modules, 1, NOW
    )
    assert len(results) == 1
    assert results[0].module == "FIM"
    assert results[0].latest_public_release.version == "4.9.4"


def test_check_public_release_announcements_suppresses_duplicate(fake_site):
    client = QualysReleaseNotesClient()
    cache = release_cache.load_cache("/nonexistent")
    modules = {"FIM": {"version": "4.9.3"}}
    already_notified = {"FIM": "4.9.4"}
    results = release_intelligence.check_public_release_announcements(
        client, cache, already_notified, modules, 1, NOW
    )
    assert results == []


def test_check_public_release_announcements_skips_current_modules(fake_site):
    client = QualysReleaseNotesClient()
    cache = release_cache.load_cache("/nonexistent")
    modules = {"FIM": {"version": "4.9.4"}}  # already at latest
    results = release_intelligence.check_public_release_announcements(
        client, cache, {}, modules, 1, NOW
    )
    assert results == []


def test_notified_state_round_trip(tmp_path):
    path = str(tmp_path / "notified.json")
    assert release_intelligence.load_notified_state(path) == {}
    release_intelligence.save_notified_state(path, {"FIM": "4.9.4"})
    assert release_intelligence.load_notified_state(path) == {"FIM": "4.9.4"}
