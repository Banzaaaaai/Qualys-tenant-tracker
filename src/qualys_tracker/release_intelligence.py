"""Business logic connecting tenant versions to public release notes.

This is the layer main.py calls. It never lets a release-note lookup
failure propagate -- every public entry point catches
`ReleaseNotesError` and reports `UpgradeStatus.RELEASE_NOTE_LOOKUP_FAILED`
instead, so a Qualys documentation outage can never affect tenant
version tracking itself (spec section 18).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime

from .models import Feature, ModuleReleaseIntelligence, ReleaseInfo, UpgradeStatus
from .release_notes import (
    QualysReleaseNotesClient,
    ReleaseNotesError,
    find_entries_for_module,
    find_entry_for_version,
)
from .release_cache import get_latest_public, get_release, put_latest_public, put_release
from .version_compare import Ordering, compare_versions, latest_version

DEFAULT_NOTIFIED_STATE_FILENAME = "public_release_notifications.json"


def resolve_tenant_release(
    client: QualysReleaseNotesClient,
    cache: dict,
    module: str,
    version: str,
    now_iso: str,
) -> ReleaseInfo | None:
    """Find the official release note matching `module`'s exact tenant
    `version`. Returns None (never raises for a plain "not found") when
    no exact match exists in the index -- Strategy 4, no fabrication.
    Raises ReleaseNotesError only for a genuine fetch/parse failure.
    """
    cached = get_release(cache, module, version)
    if cached is not None:
        return cached

    entries = client.fetch_index_entries()
    candidates = find_entries_for_module(entries, module)
    match = find_entry_for_version(candidates, version)
    if match is None:
        return None

    detail = client.fetch_release_detail(match.url)
    release = ReleaseInfo(
        version=version,
        url=match.url,
        release_date=detail.release_date,
        title=detail.title,
        features=[Feature(t, d) for t, d in detail.features],
    )
    put_release(cache, module, version, release, source="qualys_release_notes_index", now_iso=now_iso)
    return release


def resolve_latest_public(
    client: QualysReleaseNotesClient,
    cache: dict,
    module: str,
    ttl_days: int,
    now_iso: str,
) -> tuple[ReleaseInfo | None, bool]:
    """Find the latest publicly announced version for `module`.

    Returns (release_or_none, order_confident). `release` is None when
    no candidates were found at all, OR when candidates were found but
    ordering among them could not be safely determined (see
    version_compare.py) -- `order_confident` distinguishes the two so
    the caller can report PUBLIC_RELEASE_NOT_FOUND vs
    VERSION_ORDER_UNKNOWN accurately.
    """
    now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    cached = get_latest_public(cache, module, ttl_days, now)
    if cached is not None:
        return cached, True

    entries = client.fetch_index_entries()
    candidates = find_entries_for_module(entries, module)
    if not candidates:
        return None, True

    versions = [c.version_text for c in candidates]
    result = latest_version(versions)
    if not result.confident:
        return None, False

    match = next(c for c in candidates if c.version_text == result.version)
    detail = client.fetch_release_detail(match.url)

    release = ReleaseInfo(
        version=result.version,
        url=match.url,
        release_date=detail.release_date,
        title=detail.title,
        features=[Feature(t, d) for t, d in detail.features],
    )
    put_latest_public(cache, module, release, now_iso=now_iso)
    return release, True


def build_module_intelligence(
    client: QualysReleaseNotesClient,
    cache: dict,
    module: str,
    tenant_version: str,
    ttl_days: int,
    now_iso: str,
    include_tenant_release: bool = True,
) -> ModuleReleaseIntelligence:
    """Correlate one module's tenant version against public release notes.

    Set `include_tenant_release=False` to skip fetching "capabilities
    available now" (used by the lightweight standalone public-release
    scan, which only needs the latest-public comparison for every
    unchanged module and shouldn't pay for a full release-detail fetch
    per module every run).
    """
    lookup_error: str | None = None
    tenant_release: ReleaseInfo | None = None

    if include_tenant_release:
        try:
            tenant_release = resolve_tenant_release(client, cache, module, tenant_version, now_iso)
        except ReleaseNotesError as exc:
            lookup_error = str(exc)

    latest_release: ReleaseInfo | None = None
    order_confident = True
    if lookup_error is None:
        try:
            latest_release, order_confident = resolve_latest_public(client, cache, module, ttl_days, now_iso)
        except ReleaseNotesError as exc:
            lookup_error = str(exc)

    if lookup_error is not None:
        status = UpgradeStatus.RELEASE_NOTE_LOOKUP_FAILED
    elif latest_release is None:
        status = UpgradeStatus.PUBLIC_RELEASE_NOT_FOUND if order_confident else UpgradeStatus.VERSION_ORDER_UNKNOWN
    else:
        ordering = compare_versions(latest_release.version, tenant_version)
        if ordering == Ordering.UNKNOWN:
            status = UpgradeStatus.VERSION_ORDER_UNKNOWN
        elif ordering == Ordering.NEWER:
            status = UpgradeStatus.PUBLIC_NEWER_VERSION_AVAILABLE
        else:
            status = UpgradeStatus.CURRENT

    return ModuleReleaseIntelligence(
        module=module,
        tenant_version=tenant_version,
        tenant_release=tenant_release,
        latest_public_release=latest_release,
        upgrade_status=status,
        error=lookup_error,
    )


# --- Standalone "Qualys announced something, tenant hasn't changed" ------


def load_notified_state(path: str) -> dict[str, str]:
    """module -> the latest public version we've already sent a
    standalone announcement email about (see spec section 21-22)."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_notified_state(path: str, state: dict[str, str]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
            fh.write("\n")
        with open(tmp_path, encoding="utf-8") as fh:
            json.load(fh)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def check_public_release_announcements(
    client: QualysReleaseNotesClient,
    cache: dict,
    notified_state: dict[str, str],
    modules: dict[str, dict],
    ttl_days: int,
    now_iso: str,
) -> list[ModuleReleaseIntelligence]:
    """For modules whose tenant version did NOT change this run, check
    whether Qualys has announced a public version newer than both the
    tenant's current version and whatever was last notified about.

    Idempotent by design (spec section 22): a module is only included
    once per distinct newly-discovered public version. The caller is
    responsible for updating `notified_state` after a successful send.
    """
    results = []
    for module, info in modules.items():
        tenant_version = info.get("version")
        if not tenant_version:
            continue
        intel = build_module_intelligence(
            client, cache, module, tenant_version, ttl_days, now_iso, include_tenant_release=False
        )
        if intel.upgrade_status != UpgradeStatus.PUBLIC_NEWER_VERSION_AVAILABLE:
            continue
        already_notified = notified_state.get(module)
        if already_notified == intel.latest_public_release.version:
            continue
        results.append(intel)
    return results
