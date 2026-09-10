"""Append-only version change history (version_history.json).

Only real changes are ever appended -- a run with no changes appends
nothing, guaranteeing idempotency (section 26 of the spec).
"""

from __future__ import annotations

import json
import os
import tempfile

from .models import ModuleChange

DEFAULT_HISTORY_FILENAME = "version_history.json"


def load_history(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        # History is diagnostic, not authoritative state: if it's unreadable,
        # start a fresh list rather than blocking the run.
        return []
    return data if isinstance(data, list) else []


def append_history_entries(
    path: str, changes: list[ModuleChange], timestamp: str
) -> list[dict]:
    """Append one entry per change and atomically save. Returns the full list."""
    if not changes:
        return load_history(path)

    history = load_history(path)
    history.extend(change.to_history_entry(timestamp) for change in changes)
    _save_atomic(path, history)
    return history


def _save_atomic(path: str, history: list[dict]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(history, fh, indent=2, sort_keys=True)
            fh.write("\n")
        with open(tmp_path, encoding="utf-8") as fh:
            json.load(fh)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
