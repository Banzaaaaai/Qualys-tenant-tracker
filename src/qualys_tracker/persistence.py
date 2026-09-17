"""Recoverable multi-file state updates for a single tracker writer."""

import json
import os

from .state import save_snapshot_atomic

JOURNAL = "pending_transaction.json"
ALLOWED_FILES = {
    "tenant_snapshot.json", "version_history.json", "notification_outbox.json",
    "public_release_notifications.json",
}


def recover(directory: str) -> None:
    path = os.path.join(directory, JOURNAL)
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as stream:
        updates = json.load(stream)
    if not isinstance(updates, dict) or not updates or set(updates) - ALLOWED_FILES:
        raise ValueError("Invalid recovery journal; restore it before continuing")
    for filename, value in updates.items():
        save_snapshot_atomic(os.path.join(directory, filename), value)
    os.remove(path)


def commit(directory: str, updates: dict) -> None:
    if not updates or set(updates) - ALLOWED_FILES:
        raise ValueError("Invalid transaction targets")
    recover(directory)
    save_snapshot_atomic(os.path.join(directory, JOURNAL), updates)
    recover(directory)
