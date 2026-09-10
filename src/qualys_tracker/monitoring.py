"""Staleness detection, derived entirely from run_log.json.

No extra state file is needed: "last successful check" is the most
recent run_log entry with api_success == True, and "last time we
already alerted" is the most recent entry with stale_alert == True.
That keeps alert suppression correct across runs without adding a
fourth JSON file to the repo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class StalenessStatus:
    is_stale: bool
    should_alert: bool
    last_success_at: str | None
    last_error: str | None
    days_since_success: float | None


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def check_staleness(
    run_log: list[dict],
    now: datetime,
    stale_after_days: int,
    suppression_days: int,
) -> StalenessStatus:
    last_success_at: str | None = None
    last_error: str | None = None
    last_alert_at: str | None = None

    for entry in run_log:
        if entry.get("api_success"):
            last_success_at = entry.get("timestamp")
        elif entry.get("error"):
            last_error = entry.get("error")
        if entry.get("stale_alert"):
            last_alert_at = entry.get("timestamp")

    if last_success_at is None:
        # Never had a single successful check -- treat as stale so an
        # alert can fire once the suppression window allows it.
        is_stale = True
        days_since_success = None
    else:
        elapsed = now - _parse_iso(last_success_at)
        days_since_success = elapsed.total_seconds() / 86400
        is_stale = elapsed >= timedelta(days=stale_after_days)

    should_alert = False
    if is_stale:
        if last_alert_at is None:
            should_alert = True
        else:
            since_alert = now - _parse_iso(last_alert_at)
            should_alert = since_alert >= timedelta(days=suppression_days)

    return StalenessStatus(
        is_stale=is_stale,
        should_alert=should_alert,
        last_success_at=last_success_at,
        last_error=last_error,
        days_since_success=days_since_success,
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
