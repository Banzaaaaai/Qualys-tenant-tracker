"""Persistent cache for release-note lookups (release_notes_cache.json).

Two independent sections:

- `releases`: exact module+version -> full ReleaseInfo. A specific
  version's release notes don't change after publication, so these
  entries never expire.
- `latest_public`: module -> the most recently discovered "latest
  public version" info, with a `retrieved_at` timestamp. These DO
  expire, after RELEASE_NOTES_CACHE_TTL_DAYS, so the tracker
  periodically re-checks for newer announcements without re-scraping
  on every single run.

Same atomic-write discipline as state.py/history.py: a cache is
allowed to be rebuilt from scratch if corrupted (it's non-authoritative
-- unlike tenant_snapshot.json, losing it just costs a few re-fetches),
but a successful write is still atomic to avoid partial JSON.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta

from .models import ReleaseInfo

DEFAULT_CACHE_FILENAME = "release_notes_cache.json"


def _empty_cache() -> dict:
    return {"releases": {}, "latest_public": {}}


def _cache_key(module: str, version: str) -> str:
    return f"{module}|{version}"


def load_cache(path: str) -> dict:
    if not os.path.exists(path):
        return _empty_cache()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return _empty_cache()
    if not isinstance(data, dict):
        return _empty_cache()
    data.setdefault("releases", {})
    data.setdefault("latest_public", {})
    return data


def save_cache(path: str, cache: dict) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2, sort_keys=True)
            fh.write("\n")
        with open(tmp_path, encoding="utf-8") as fh:
            json.load(fh)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def get_release(cache: dict, module: str, version: str) -> ReleaseInfo | None:
    entry = cache["releases"].get(_cache_key(module, version))
    if entry is None:
        return None
    return ReleaseInfo.from_dict(entry["release"])


def put_release(cache: dict, module: str, version: str, release: ReleaseInfo, source: str, now_iso: str) -> None:
    cache["releases"][_cache_key(module, version)] = {
        "module": module,
        "version": version,
        "release": release.to_dict(),
        "source": source,
        "retrieved_at": now_iso,
    }


def get_latest_public(cache: dict, module: str, ttl_days: int, now: datetime) -> ReleaseInfo | None:
    """Return the cached "latest public" ReleaseInfo for `module`, or None
    if there is no cache entry or it has expired past `ttl_days`."""
    entry = cache["latest_public"].get(module)
    if entry is None:
        return None
    retrieved_at = entry.get("retrieved_at")
    if retrieved_at:
        try:
            retrieved = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
            if now - retrieved > timedelta(days=ttl_days):
                return None
        except ValueError:
            return None
    return ReleaseInfo.from_dict(entry["release"])


def put_latest_public(cache: dict, module: str, release: ReleaseInfo, now_iso: str) -> None:
    cache["latest_public"][module] = {
        "release": release.to_dict(),
        "retrieved_at": now_iso,
    }
