"""DST-robust schedule guard.

GitHub Actions cron is fixed in UTC, but the desired run times are
expressed in local time. The workflow registers both seasonal UTC offsets.
This guard permits due slots within a late-arrival tolerance. main.py uses
successful run-log slot IDs to suppress duplicate seasonal triggers.

Manual runs (workflow_dispatch) always bypass this guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo


@dataclass
class ScheduleCheck:
    should_run: bool
    local_time: str
    timezone: str
    slot: str | None = None


def _parse_hhmm(value: str) -> dt_time:
    hours, minutes = value.strip().split(":")
    return dt_time(hour=int(hours), minute=int(minutes))


def is_within_scheduled_window(
    now_utc: datetime,
    timezone_name: str,
    local_run_times: list[str],
    tolerance_minutes: int,
) -> ScheduleCheck:
    local_now = now_utc.astimezone(ZoneInfo(timezone_name))
    local_time_str = local_now.strftime("%H:%M")

    # Only run after a slot is due. This skips the early winter trigger.
    # The persisted slot ID suppresses the late summer trigger and delayed duplicates.
    candidates = []
    for days_back in (0, 1):
        for target in local_run_times:
            target_time = _parse_hhmm(target)
            target_dt = (local_now - timedelta(days=days_back)).replace(
                hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0
            )
            elapsed = local_now - target_dt
            if timedelta(0) <= elapsed <= timedelta(minutes=tolerance_minutes):
                candidates.append(target_dt)
    slot = None
    if candidates:
        target_dt = max(candidates)
        slot = f"{timezone_name}:{target_dt.strftime('%Y-%m-%dT%H:%M')}"
    return ScheduleCheck(bool(candidates), local_time_str, timezone_name, slot)
