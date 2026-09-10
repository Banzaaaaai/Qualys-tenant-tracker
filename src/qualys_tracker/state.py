"""Persistent tenant snapshot: load, validate, and atomically save.

The guiding rule (spec sections 11-12, 24-26): a bad read must never
lead to a bad write. If the existing snapshot can't be parsed, or the
new one fails validation, the file on disk is left untouched.
"""

from __future__ import annotations

import json
import os
import tempfile

from .models import ComparisonResult

DEFAULT_SNAPSHOT_FILENAME = "tenant_snapshot.json"


class StateCorruptionError(Exception):
    """The on-disk snapshot exists but is not valid JSON / not the expected shape.

    This is intentionally distinct from "no snapshot yet" (first run):
    callers must NOT treat a corrupted file as a fresh baseline, since
    that would silently discard history continuity and could mask a
    real problem.
    """


def load_snapshot(path: str) -> dict | None:
    """Return the parsed snapshot, or None if no snapshot file exists yet.

    Raises StateCorruptionError if the file exists but cannot be parsed
    or does not have the expected top-level shape.
    """
    if not os.path.exists(path):
        return None

    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise StateCorruptionError(f"Could not parse existing snapshot: {exc}") from exc

    if not isinstance(data, dict) or "modules" not in data or not isinstance(
        data.get("modules"), dict
    ):
        raise StateCorruptionError(
            "Existing snapshot is missing the expected 'modules' object"
        )

    return data


def build_snapshot(
    previous: dict | None,
    comparison: ComparisonResult,
    current_modules_by_name: dict[str, "ModuleVersion"],  # noqa: F821
    timestamp: str,
    tenant_api_url: str,
    tenant_identifier: str,
) -> dict:
    """Build the new snapshot dict from the comparison result.

    First-seen/last-changed bookkeeping: new modules get both stamped
    to `timestamp`; changed modules keep first_seen and bump
    last_changed; unchanged modules keep both untouched; removed
    modules are dropped from the modules map (they are no longer part
    of "what the tenant currently reports"), but remain visible via
    version_history.json.
    """
    previous_modules = (previous or {}).get("modules", {})
    modules: dict[str, dict] = {}

    changed_names = {c.module for c in comparison.changed}
    new_names = {c.module for c in comparison.new}

    for name in comparison.unchanged:
        modules[name] = previous_modules[name]

    for name in changed_names:
        prior = previous_modules.get(name, {})
        current = current_modules_by_name[name]
        modules[name] = {
            "api_field": current.api_field,
            "version": current.version,
            "first_seen": prior.get("first_seen", timestamp),
            "last_changed": timestamp,
        }

    for name in new_names:
        current = current_modules_by_name[name]
        modules[name] = {
            "api_field": current.api_field,
            "version": current.version,
            "first_seen": timestamp,
            "last_changed": timestamp,
        }

    return {
        "tenant": {
            "api_url": tenant_api_url,
            "identifier": tenant_identifier,
        },
        "last_checked": timestamp,
        "modules": modules,
    }


def save_snapshot_atomic(path: str, snapshot: dict) -> None:
    """Write `snapshot` to `path` atomically.

    Writes to a sibling temp file, verifies it round-trips through
    `json.load`, then uses `os.replace` (atomic on POSIX and Windows)
    to install it. A crash mid-write leaves the original file intact.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2, sort_keys=True)
            fh.write("\n")

        with open(tmp_path, encoding="utf-8") as fh:
            json.load(fh)  # Validate round-trip before installing.

        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
