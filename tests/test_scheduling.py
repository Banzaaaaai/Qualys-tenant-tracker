from datetime import datetime, timezone

from qualys_tracker.scheduling import is_within_scheduled_window


def test_within_tolerance_of_morning_run():
    # 08:45 Europe/Amsterdam in winter (CET, UTC+1) is 07:45 UTC.
    now = datetime(2026, 1, 15, 7, 50, tzinfo=timezone.utc)
    check = is_within_scheduled_window(now, "Europe/Amsterdam", ["08:45", "16:45"], 90)
    assert check.should_run


def test_outside_tolerance_is_skipped():
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    check = is_within_scheduled_window(now, "Europe/Amsterdam", ["08:45", "16:45"], 90)
    assert not check.should_run


def test_dst_summer_offset_still_matches():
    # 08:45 Europe/Amsterdam in summer (CEST, UTC+2) is 06:45 UTC.
    now = datetime(2026, 7, 15, 6, 50, tzinfo=timezone.utc)
    check = is_within_scheduled_window(now, "Europe/Amsterdam", ["08:45", "16:45"], 90)
    assert check.should_run
