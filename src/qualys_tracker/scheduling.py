"""DST-robust schedule guard.

GitHub Actions cron is fixed in UTC, but the desired run times are
expressed in local time (e.g. Europe/Amsterdam 08:45/16:45), which
shifts by an hour across DST transitions. Rather than editing cron
expressions twice a year, the workflow schedules a cron trigger for
*each* UTC offset the target timezone can be in (see
.github/workflows/tracker.yml), and this guard decides -- using the
real IANA timezone database via zoneinfo -- whether "now" actually
falls within tolerance of one of the configured local run times. If it
doesn't, the run is a no-op: nothing is logged, nothing is sent.

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

    should_run = False
    for target in local_run_times:
        target_time = _parse_hhmm(target)
        target_dt = local_now.replace(
            hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0
        )
        if abs((local_now - target_dt)) <= timedelta(minutes=tolerance_minutes):
            should_run = True
            break

    return ScheduleCheck(
        should_run=should_run, local_time=local_time_str, timezone=timezone_name
    )
