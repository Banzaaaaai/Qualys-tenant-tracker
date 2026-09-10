from datetime import datetime, timezone

from qualys_tracker.monitoring import check_staleness


def test_no_prior_runs_is_stale_and_alerts():
    status = check_staleness([], datetime(2026, 1, 10, tzinfo=timezone.utc), 3, 3)
    assert status.is_stale
    assert status.should_alert


def test_recent_success_is_not_stale():
    run_log = [{"timestamp": "2026-01-09T00:00:00Z", "api_success": True}]
    status = check_staleness(run_log, datetime(2026, 1, 10, tzinfo=timezone.utc), 3, 3)
    assert not status.is_stale
    assert not status.should_alert


def test_old_failure_is_stale_after_threshold():
    run_log = [
        {"timestamp": "2026-01-01T00:00:00Z", "api_success": True},
        {"timestamp": "2026-01-05T00:00:00Z", "api_success": False, "error": "HTTP 503"},
    ]
    status = check_staleness(run_log, datetime(2026, 1, 10, tzinfo=timezone.utc), 3, 3)
    assert status.is_stale
    assert status.last_error == "HTTP 503"
    assert status.should_alert


def test_alert_suppressed_within_window():
    run_log = [
        {"timestamp": "2026-01-01T00:00:00Z", "api_success": True},
        {"timestamp": "2026-01-08T00:00:00Z", "api_success": False, "stale_alert": True},
    ]
    status = check_staleness(run_log, datetime(2026, 1, 10, tzinfo=timezone.utc), 3, 3)
    assert status.is_stale
    assert not status.should_alert  # alerted 2 days ago, suppression window is 3 days


def test_alert_resumes_after_suppression_window():
    run_log = [
        {"timestamp": "2026-01-01T00:00:00Z", "api_success": True},
        {"timestamp": "2026-01-05T00:00:00Z", "api_success": False, "stale_alert": True},
    ]
    status = check_staleness(run_log, datetime(2026, 1, 10, tzinfo=timezone.utc), 3, 3)
    assert status.should_alert  # 5 days since last alert, suppression is 3 days
