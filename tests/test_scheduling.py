from datetime import datetime, timezone

from qualys_tracker.config import TrackerConfig
from qualys_tracker.scheduling import is_within_scheduled_window

TZ = "Europe/Amsterdam"
SLOTS = ["08:45", "16:45"]
# Production default: a due slot stays claimable for 24h (see config.py).
DEFAULT = 1440


def test_within_tolerance_of_morning_run():
    # 08:45 Europe/Amsterdam in winter (CET, UTC+1) is 07:45 UTC.
    now = datetime(2026, 1, 15, 7, 50, tzinfo=timezone.utc)
    check = is_within_scheduled_window(now, TZ, SLOTS, DEFAULT)
    assert check.should_run


def test_dst_summer_offset_still_matches():
    # 08:45 Europe/Amsterdam in summer (CEST, UTC+2) is 06:45 UTC.
    now = datetime(2026, 7, 15, 6, 50, tzinfo=timezone.utc)
    check = is_within_scheduled_window(now, TZ, SLOTS, DEFAULT)
    assert check.should_run


def test_slot_not_yet_due_is_skipped():
    # The "wrong season" trigger fires an hour early; it must not claim the
    # 16:45 slot at 16:32 local. The earlier 08:45 slot is what it maps to,
    # and main.py skips that one because it already completed.
    now = datetime(2026, 9, 21, 14, 32, tzinfo=timezone.utc)  # 16:32 local
    check = is_within_scheduled_window(now, TZ, SLOTS, DEFAULT)
    assert check.slot == f"{TZ}:2026-09-21T08:45"


def test_badly_delayed_github_run_still_claims_its_slot():
    # Regression: GitHub delivered these crons 3-13h late and the old
    # 90-minute window no-op'd every single scheduled run, so the tracker
    # went dark for eight days while reporting success.
    for utc_hour, utc_minute, expected in [
        (13, 21, "2026-09-21T08:45"),  # 397 min late
        (14, 32, "2026-09-21T08:45"),  # 468 min late
        (19, 45, "2026-09-21T16:45"),  # 300 min late
        (20, 7, "2026-09-21T16:45"),   # 322 min late
    ]:
        now = datetime(2026, 9, 21, utc_hour, utc_minute, tzinfo=timezone.utc)
        check = is_within_scheduled_window(now, TZ, SLOTS, DEFAULT)
        assert check.should_run, f"{utc_hour}:{utc_minute:02d}Z was rejected"
        assert check.slot == f"{TZ}:{expected}"


def test_each_local_slot_gets_a_distinct_slot_id():
    # The slot ID is the only thing preventing a second check per slot, so
    # the two daily slots must never collide.
    morning = is_within_scheduled_window(
        datetime(2026, 9, 21, 13, 21, tzinfo=timezone.utc), TZ, SLOTS, DEFAULT
    )
    evening = is_within_scheduled_window(
        datetime(2026, 9, 21, 19, 45, tzinfo=timezone.utc), TZ, SLOTS, DEFAULT
    )
    assert morning.slot != evening.slot


def test_stale_slot_beyond_lateness_bound_is_abandoned():
    # 26h after the last due slot: too old to catch up on.
    now = datetime(2026, 9, 22, 23, 30, tzinfo=timezone.utc)
    check = is_within_scheduled_window(now, TZ, SLOTS, 60)
    assert not check.should_run


def test_default_config_tolerates_real_github_scheduler_delay():
    # Guards against a future "tighten the window" change silently
    # reintroducing the outage.
    config = TrackerConfig(tenant_identifier="t")
    assert config.schedule_guard_tolerance_minutes >= 13 * 60
