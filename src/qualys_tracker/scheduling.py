"""DST-robust schedule guard.

GitHub Actions cron is fixed in UTC, but the desired run times are
expressed in local time. The workflow registers both seasonal UTC offsets.

GitHub does NOT deliver scheduled runs on time: on this repo the cron has
arrived anywhere from ~3 to ~13 hours after its nominal minute. So this
guard deliberately does NOT enforce a narrow window around a slot -- doing
that silently no-ops every scheduled run and the tracker goes dark without
failing (see docs/operations.md). Instead it attributes the run to the most
recent slot that is already DUE, however late the runner turned up, and
main.py uses the persisted slot ID to skip a slot that already completed
successfully. That is what suppresses the duplicate seasonal trigger and
any redundant retry, not the elapsed time.

`max_lateness_minutes` therefore only bounds how stale a missed slot may be
before it is abandoned rather than caught up. Manual runs
(workflow_dispatch) always bypass this guard.
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
    max_lateness_minutes: int,
) -> ScheduleCheck:
    local_now = now_utc.astimezone(ZoneInfo(timezone_name))
    local_time_str = local_now.strftime("%H:%M")

    # Only run once a slot is actually due; this is what skips the "wrong
    # season" trigger that fires an hour early. There is no upper bound on
    # lateness beyond max_lateness_minutes, because a late runner is the
    # normal case, not an anomaly.
    candidates = []
    for days_back in (0, 1):
        for target in local_run_times:
            target_time = _parse_hhmm(target)
            target_dt = (local_now - timedelta(days=days_back)).replace(
                hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0
            )
            elapsed = local_now - target_dt
            if timedelta(0) <= elapsed <= timedelta(minutes=max_lateness_minutes):
                candidates.append(target_dt)
    slot = None
    if candidates:
        target_dt = max(candidates)
        slot = f"{timezone_name}:{target_dt.strftime('%Y-%m-%dT%H:%M')}"
    return ScheduleCheck(bool(candidates), local_time_str, timezone_name, slot)
