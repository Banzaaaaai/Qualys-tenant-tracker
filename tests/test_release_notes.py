import os

import pytest
import requests

from qualys_tracker.release_notes import (
    QualysReleaseNotesClient,
    ReleaseNotesError,
    find_entries_for_module,
    find_entry_for_version,
    parse_index,
    parse_release_detail,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "release_notes")


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def index_html():
    return _read("index_sample.html")


def test_parse_index_extracts_entries(index_html):
    entries = parse_index(index_html)
    names = {e.product_name for e in entries}
    assert "File Integrity Monitoring" in names
    assert "Web Application Scanning" in names
    assert "Container Security" in names


def test_parse_index_skips_api_companion_pages(index_html):
    entries = parse_index(index_html)
    urls = [e.url for e in entries]
    assert not any(u.endswith("_api.htm") for u in urls)
    was_versions = {e.version_text for e in entries if e.product_name == "Web Application Scanning"}
    assert was_versions == {"6.0.1", "6.0.0"}  # the "6.0.0 API" duplicate is excluded


def test_find_entries_for_module_uses_hint_table(index_html):
    entries = parse_index(index_html)
    fim_entries = find_entries_for_module(entries, "FIM")
    assert {e.version_text for e in fim_entries} == {"4.9.4", "4.9.3"}


def test_find_entries_for_module_with_known_hint_matches_full_product_name(index_html):
    entries = parse_index(index_html)
    cs_entries = find_entries_for_module(entries, "CS")  # hinted to "Container Security"
    assert len(cs_entries) == 1
    assert cs_entries[0].product_name == "Container Security"


def test_find_entries_for_unmapped_module_falls_back_to_code_as_substring(index_html):
    entries = parse_index(index_html)
    # No hint entry for this made-up code, and it isn't a substring of any
    # product name either -- correctly finds nothing rather than guessing.
    assert find_entries_for_module(entries, "ZZZ") == []

    # A module code that happens to literally match part of a product name
    # (as a fallback, not a hint) still resolves via the substring check.
    fim_entries = find_entries_for_module(entries, "Container Security")
    assert len(fim_entries) == 1


def test_find_entry_for_exact_version_match(index_html):
    entries = find_entries_for_module(parse_index(index_html), "FIM")
    match = find_entry_for_version(entries, "4.9.4")
    assert match is not None
    assert match.url.endswith("release_4_9_4.htm")


def test_find_entry_for_version_not_found_returns_none(index_html):
    entries = find_entries_for_module(parse_index(index_html), "FIM")
    assert find_entry_for_version(entries, "9.9.9") is None


def test_parse_release_detail_extracts_title_date_and_features():
    html = _read("fim_4_9_4_release.html")
    detail = parse_release_detail(html, "https://docs.qualys.com/en/fim/.../release_4_9_4.htm")
    assert detail.title == "File Integrity Monitoring Release 4.9.4"
    assert detail.release_date == "September 09, 2026"
    assert len(detail.features) == 3
    assert detail.features[0][0] == "Create Incident for Individual Events"
    assert "individual events" in detail.features[0][1].lower()


def test_parse_release_detail_with_no_features_still_parses():
    html = _read("no_features_release.html")
    detail = parse_release_detail(html, "https://docs.qualys.com/en/was/.../release_6_0_1.htm")
    assert detail.title == "Web Application Scanning Release 6.0.1"
    assert detail.features == []


def test_parse_release_detail_malformed_raises():
    html = _read("malformed_release.html")
    with pytest.raises(ReleaseNotesError):
        parse_release_detail(html, "https://docs.qualys.com/bad.htm")


# --- Client-level tests (HTTP mocked) ---------------------------------


class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self.ok = 200 <= status_code < 300


def test_client_refuses_non_official_host():
    client = QualysReleaseNotesClient(index_url="https://evil-mirror.example.com/release-notes")
    with pytest.raises(ReleaseNotesError):
        client.fetch_index_entries()


def test_client_fetches_and_memoizes_index(monkeypatch, index_html):
    calls = {"count": 0}

    def fake_get(url, headers=None, timeout=None):
        calls["count"] += 1
        return _FakeResponse(200, index_html)

    monkeypatch.setattr(requests, "get", fake_get)
    client = QualysReleaseNotesClient()
    client.fetch_index_entries()
    client.fetch_index_entries()
    assert calls["count"] == 1  # memoized -- second call didn't re-fetch


def test_client_retries_transient_failure_then_succeeds(monkeypatch, index_html):
    responses = [_FakeResponse(503), _FakeResponse(200, index_html)]

    def fake_get(url, headers=None, timeout=None):
        return responses.pop(0)

    monkeypatch.setattr(requests, "get", fake_get)
    client = QualysReleaseNotesClient(max_retries=3, sleep_fn=lambda s: None)
    entries = client.fetch_index_entries()
    assert len(entries) > 0


def test_client_raises_after_website_unavailable(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse(503)

    monkeypatch.setattr(requests, "get", fake_get)
    client = QualysReleaseNotesClient(max_retries=2, sleep_fn=lambda s: None)
    with pytest.raises(ReleaseNotesError):
        client.fetch_index_entries()
