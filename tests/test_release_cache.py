import json

from qualys_tracker import release_cache
from qualys_tracker.models import Feature, ReleaseInfo
from qualys_tracker.release_notes import PARSE_FORMAT_VERSION


def test_load_missing_cache_returns_empty_structure(tmp_path):
    cache = release_cache.load_cache(str(tmp_path / "missing.json"))
    assert cache["releases"] == {}
    assert cache["latest_public"] == {}
    assert cache["parse_format_version"] == PARSE_FORMAT_VERSION


def test_load_corrupted_cache_returns_empty_structure_not_error(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{not valid json", encoding="utf-8")
    cache = release_cache.load_cache(str(path))
    assert cache["releases"] == {}
    assert cache["latest_public"] == {}


def test_put_and_get_release_round_trip(tmp_path):
    cache = release_cache.load_cache(str(tmp_path / "cache.json"))
    release = ReleaseInfo(
        version="4.9.4", url="https://docs.qualys.com/x.htm",
        release_date="September 09, 2026", title="FIM 4.9.4",
        features=[Feature("A", "desc")],
    )
    release_cache.put_release(cache, "FIM", "4.9.4", release, "index", "2026-09-10T00:00:00Z")
    fetched = release_cache.get_release(cache, "FIM", "4.9.4")
    assert fetched.version == "4.9.4"
    assert fetched.features[0].title == "A"


def test_get_release_missing_returns_none(tmp_path):
    cache = release_cache.load_cache(str(tmp_path / "cache.json"))
    assert release_cache.get_release(cache, "FIM", "1.0.0") is None


def test_save_is_atomic_and_round_trips(tmp_path):
    path = str(tmp_path / "cache.json")
    cache = release_cache.load_cache(path)
    release = ReleaseInfo(version="1.0", url="https://docs.qualys.com/x.htm")
    release_cache.put_release(cache, "WAF", "1.0", release, "index", "2026-09-10T00:00:00Z")
    release_cache.save_cache(path, cache)

    with open(path, encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert on_disk["releases"]["WAF|1.0"]["module"] == "WAF"

    reloaded = release_cache.load_cache(path)
    assert release_cache.get_release(reloaded, "WAF", "1.0").version == "1.0"


def test_cache_from_an_older_parser_is_discarded(tmp_path):
    """Regression: `releases` entries never expire, so a parser fix could
    not reach an already-cached module. The "Applicable for:" descriptions
    survived the parser fix because of exactly this."""
    path = tmp_path / "cache.json"
    path.write_text(
        json.dumps({
            "parse_format_version": PARSE_FORMAT_VERSION - 1,
            "releases": {"TC|2.27": {"module": "TC", "version": "2.27", "release": {
                "version": "2.27", "url": "https://docs.qualys.com/tc.htm",
                "features": [{"title": "Wildcard Support", "description": "Applicable for:"}],
            }}},
            "latest_public": {},
        }),
        encoding="utf-8",
    )
    cache = release_cache.load_cache(str(path))
    assert cache["releases"] == {}
    assert release_cache.get_release(cache, "TC", "2.27") is None


def test_cache_from_the_current_parser_is_kept(tmp_path):
    path = tmp_path / "cache.json"
    cache = release_cache.load_cache(str(path))
    release = ReleaseInfo(
        version="2.27", url="https://docs.qualys.com/tc.htm",
        release_date=None, title="TotalCloud 2.27",
        features=[Feature("Wildcard Support", "match tags by pattern")],
    )
    release_cache.put_release(cache, "TC", "2.27", release, "index", "2026-09-22T00:00:00Z")
    release_cache.save_cache(str(path), cache)

    reloaded = release_cache.load_cache(str(path))
    assert release_cache.get_release(reloaded, "TC", "2.27") is not None
