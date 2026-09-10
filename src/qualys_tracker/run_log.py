"""Bounded run log (run_log.json) with retention.

Every execution appends exactly one record, and the file is capped at
`max_entries` (oldest entries dropped first) so it never grows without
bound.
"""

from __future__ import annotations

import json
import os
import tempfile

DEFAULT_RUN_LOG_FILENAME = "run_log.json"


def load_run_log(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def append_run_record(path: str, record: dict, max_entries: int = 500) -> list[dict]:
    log = load_run_log(path)
    log.append(record)
    if len(log) > max_entries:
        log = log[-max_entries:]
    _save_atomic(path, log)
    return log


def _save_atomic(path: str, log: list[dict]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(log, fh, indent=2, sort_keys=True)
            fh.write("\n")
        with open(tmp_path, encoding="utf-8") as fh:
            json.load(fh)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
