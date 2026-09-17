"""Durable email delivery with stable IDs and at-least-once retry semantics."""

import hashlib
import json
import os

from .config import ConfigError, EmailConfig
from .notifier import EmailNotifier, NotificationError
from .state import save_snapshot_atomic

FILENAME = "notification_outbox.json"


class RenderNotifier(EmailNotifier):
    """Use the real email templates without SMTP or credentials."""

    def __init__(self):
        self.messages = []

    def _send(self, subject: str, html_body: str) -> None:
        self.messages.append({"subject": subject, "html": html_body})


def load(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as stream:
        entries = json.load(stream)
    if not isinstance(entries, list) or any(
        not isinstance(e, dict) or not all(isinstance(e.get(k), str) for k in ("id", "kind", "subject", "html"))
        for e in entries
    ):
        raise ValueError("Invalid notification outbox; restore it before continuing")
    return entries


def enqueue(entries: list[dict], messages: list[dict], kind: str, event_key: str) -> None:
    for message in messages:
        event_id = hashlib.sha256((kind + event_key + message["subject"]).encode()).hexdigest()
        if not any(e["id"] == event_id for e in entries):
            entries.append({**message, "id": event_id, "kind": kind, "attempts": 0})


def deliver(path: str) -> dict:
    entries = load(path)
    outcomes = {"sent": 0, "failed": 0, "by_kind": {}}
    for entry in list(entries):
        entry["attempts"] += 1
        try:
            notifier = EmailNotifier(EmailConfig.from_env())
            notifier._send(entry["subject"], entry["html"], message_id=entry["id"])
        except (ConfigError, NotificationError) as exc:
            entry["last_error"] = str(exc)
            outcomes["failed"] += 1
            status = "failed"
        else:
            entries.remove(entry)
            outcomes["sent"] += 1
            status = "sent"
        counts = outcomes["by_kind"].setdefault(entry["kind"], {"sent": 0, "failed": 0})
        counts[status] += 1
        save_snapshot_atomic(path, entries)
    return outcomes
