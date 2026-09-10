"""Orchestrates one execution of the tracker.

Flow: authenticate -> fetch -> validate/parse -> compare against the
stored snapshot -> persist -> notify -> log the run. See README.md for
the full architecture diagram and the failure-mode table.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from datetime import datetime, timezone

from . import comparison, history, release_cache, release_intelligence, report
from . import run_log as run_log_module
from . import state as state_module
from .api import QualysAuthError, QualysClient, QualysAPIError
from .config import ConfigError, EmailConfig, QualysConfig, ReleaseNotesConfig, TrackerConfig
from .models import UpgradeStatus
from .monitoring import check_staleness, utcnow
from .notifier import EmailNotifier, NotificationError
from .parser import ParserError, parse_portal_version_response
from .release_notes import QualysReleaseNotesClient
from .scheduling import is_within_scheduled_window

SNAPSHOT_FILENAME = state_module.DEFAULT_SNAPSHOT_FILENAME
HISTORY_FILENAME = history.DEFAULT_HISTORY_FILENAME
RUN_LOG_FILENAME = run_log_module.DEFAULT_RUN_LOG_FILENAME
REPORT_JSON_FILENAME = "tenant_report.json"
REPORT_MD_FILENAME = "tenant_report.md"
RELEASE_CACHE_FILENAME = release_cache.DEFAULT_CACHE_FILENAME
PUBLIC_RELEASE_STATE_FILENAME = release_intelligence.DEFAULT_NOTIFIED_STATE_FILENAME

RESET_BASELINE_CONFIRMATION = "CONFIRM"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _paths(state_dir: str) -> dict:
    return {
        "snapshot": os.path.join(state_dir, SNAPSHOT_FILENAME),
        "history": os.path.join(state_dir, HISTORY_FILENAME),
        "run_log": os.path.join(state_dir, RUN_LOG_FILENAME),
        "report_json": os.path.join(state_dir, REPORT_JSON_FILENAME),
        "report_md": os.path.join(state_dir, REPORT_MD_FILENAME),
        "release_cache": os.path.join(state_dir, RELEASE_CACHE_FILENAME),
        "public_release_state": os.path.join(state_dir, PUBLIC_RELEASE_STATE_FILENAME),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser_ = argparse.ArgumentParser(description="Qualys Tenant Version Tracker")
    parser_.add_argument(
        "--force-notify",
        action="store_true",
        default=os.environ.get("FORCE_NOTIFY", "").strip().lower() == "true",
        help="Send the current inventory even if nothing changed.",
    )
    parser_.add_argument(
        "--reset-baseline",
        default=os.environ.get("RESET_BASELINE", ""),
        help=f"Pass exactly '{RESET_BASELINE_CONFIRMATION}' to archive the "
        "current snapshot and start a fresh baseline on this run.",
    )
    parser_.add_argument(
        "--send-test-email",
        action="store_true",
        default=os.environ.get("SEND_TEST_EMAIL", "").strip().lower() == "true",
        help="Send a test email to verify SMTP delivery, then exit.",
    )
    parser_.add_argument(
        "--ignore-schedule-guard",
        action="store_true",
        default=os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
        help="Bypass the DST schedule guard (automatic for workflow_dispatch).",
    )
    return parser_.parse_args(argv)


def _print_header() -> None:
    print("=" * 60)
    print("Qualys Tenant Version Tracker")
    print("=" * 60)
    print(f"\nStarted: {_now_iso()}\n")


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _print_header()
    started_at = time.monotonic()

    # send-test-email only exercises SMTP delivery, so it deliberately
    # does not require valid Qualys credentials to be configured.
    if args.send_test_email:
        return _handle_send_test_email()

    try:
        qualys_config = QualysConfig.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2

    tracker_config = TrackerConfig.from_env(qualys_config.api_url)
    release_config = ReleaseNotesConfig.from_env()
    paths = _paths(tracker_config.state_dir)

    if not args.ignore_schedule_guard and tracker_config.schedule_guard_enabled:
        check = is_within_scheduled_window(
            utcnow(),
            tracker_config.timezone,
            tracker_config.local_run_times,
            tracker_config.schedule_guard_tolerance_minutes,
        )
        if not check.should_run:
            print(
                f"Local time in {check.timezone} is {check.local_time}, outside "
                "the configured run window. Skipping (no-op)."
            )
            return 0

    if args.reset_baseline:
        if args.reset_baseline != RESET_BASELINE_CONFIRMATION:
            print(
                "reset-baseline requested but confirmation string did not match "
                f"'{RESET_BASELINE_CONFIRMATION}'. Refusing to reset. Aborting."
            )
            return 2
        _archive_snapshot(paths["snapshot"])

    return _run_check(paths, qualys_config, tracker_config, release_config, args, started_at)


def _handle_send_test_email() -> int:
    try:
        email_config = EmailConfig.from_env()
        notifier = EmailNotifier(email_config)
        notifier.send_test_email()
        print("Test email: SENT")
        return 0
    except (ConfigError, NotificationError) as exc:
        print(f"Test email: FAILED ({exc})")
        return 1


def _archive_snapshot(snapshot_path: str) -> None:
    if not os.path.exists(snapshot_path):
        print("reset-baseline: no existing snapshot to archive.")
        return
    archive_path = snapshot_path + f".backup-{_now_iso().replace(':', '')}"
    os.replace(snapshot_path, archive_path)
    print(f"reset-baseline: archived existing snapshot to {archive_path}")


def _run_check(
    paths: dict,
    qualys_config: QualysConfig,
    tracker_config: TrackerConfig,
    release_config: ReleaseNotesConfig,
    args: argparse.Namespace,
    started_at: float,
) -> int:
    timestamp = _now_iso()

    print("Qualys API:")
    print(f"  URL: {qualys_config.api_url}")

    client = QualysClient(
        base_url=qualys_config.api_url,
        username=qualys_config.username,
        password=qualys_config.password,
        timeout_seconds=qualys_config.timeout_seconds,
        max_retries=qualys_config.max_retries,
    )

    try:
        raw = client.get_portal_version()
        current_modules = parse_portal_version_response(raw)
    except QualysAuthError as exc:
        return _fail_run(
            paths, tracker_config, timestamp, started_at,
            error=f"Authentication failed: {exc}",
        )
    except (QualysAPIError, ParserError) as exc:
        return _fail_run(
            paths, tracker_config, timestamp, started_at,
            error=str(exc),
        )

    print("  Status: SUCCESS\n")

    try:
        previous_snapshot = state_module.load_snapshot(paths["snapshot"])
    except state_module.StateCorruptionError as exc:
        return _fail_run(
            paths, tracker_config, timestamp, started_at,
            error=f"Snapshot validation failed, refusing to overwrite: {exc}",
        )

    is_first_run = previous_snapshot is None
    previous_modules = (previous_snapshot or {}).get("modules", {})
    comparison_result = comparison.compare(previous_modules, current_modules)
    current_by_name = {m.module: m for m in current_modules}

    new_snapshot = state_module.build_snapshot(
        previous_snapshot,
        comparison_result,
        current_by_name,
        timestamp,
        qualys_config.api_url,
        tracker_config.tenant_identifier,
    )
    state_module.save_snapshot_atomic(paths["snapshot"], new_snapshot)

    if not is_first_run:
        history.append_history_entries(
            paths["history"], comparison_result.all_changes, timestamp
        )
    else:
        # Record the baseline for future "when did this first appear" queries.
        history.append_history_entries(
            paths["history"], comparison_result.new, timestamp
        )

    report.write_json_report(paths["report_json"], new_snapshot)
    report.write_markdown_report(paths["report_md"], new_snapshot)

    print("Modules:")
    print(f"  Total: {comparison_result.total_current}")
    print(f"  Changed: {len(comparison_result.changed)}")
    print(f"  New: {len(comparison_result.new)}")
    print(f"  Removed: {len(comparison_result.removed)}\n")

    if comparison_result.changed or comparison_result.removed:
        print("Changes:")
        for change in [*comparison_result.changed, *comparison_result.removed]:
            print(f"  {change.module:<12}{change.old_version or '-':<15}-> {change.new_version or '(removed)'}")
        print()

    # --- Public release-notes correlation (spec: an additional layer   --
    # --- after tenant version detection; never allowed to affect       --
    # --- tenant tracking above, or the snapshot/history already saved) --
    release_intel_by_module: dict | None = None
    release_notes_lookup_success: bool | None = None
    release_client: QualysReleaseNotesClient | None = None
    release_cache_data: dict | None = None

    changed_or_new_names = [c.module for c in comparison_result.changed] + [
        c.module for c in comparison_result.new
    ]

    if changed_or_new_names and not is_first_run:
        # Skipped entirely on the baseline run: every module looks "new"
        # on a first run, and firing dozens of external lookups for a
        # from-scratch inventory (see README) isn't worth the latency
        # for an email that (by default) won't even be sent.
        release_cache_data = release_cache.load_cache(paths["release_cache"])
        release_client = QualysReleaseNotesClient(release_config.index_url)
        release_intel_by_module = {}
        release_notes_lookup_success = True
        print("Public release-note correlation:")
        for name in changed_or_new_names:
            version = current_by_name[name].version
            intel = release_intelligence.build_module_intelligence(
                release_client, release_cache_data, name, version,
                release_config.cache_ttl_days, timestamp,
            )
            release_intel_by_module[name] = intel
            if intel.upgrade_status == UpgradeStatus.RELEASE_NOTE_LOOKUP_FAILED:
                release_notes_lookup_success = False
            print(f"  {name:<12}tenant={version:<15}status={intel.upgrade_status.value}")
        print()

    email_sent = False
    email_failed = False

    try:
        email_config = EmailConfig.from_env()
        notifier = EmailNotifier(email_config)

        # A first run's comparison naturally reports every module as "new"
        # (has_changes is True), but that must never be treated as a real
        # change notification -- it's the baseline, handled separately.
        if is_first_run:
            if tracker_config.initial_run_notify:
                notifier.send_initial_baseline_notification(
                    new_snapshot["modules"], tracker_config.tenant_identifier,
                    timestamp, tracker_config.github_run_url,
                )
                email_sent = True
            elif args.force_notify:
                # Explicit request for a forced/manual send wins even with
                # INITIAL_RUN_NOTIFY=false -- the user asked for an email
                # right now, not for the baseline-suppression default.
                notifier.send_forced_notification(
                    new_snapshot["modules"], tracker_config.tenant_identifier,
                    timestamp, tracker_config.github_run_url,
                )
                email_sent = True
            else:
                print("Email:\n  Notification: SKIPPED (initial baseline, INITIAL_RUN_NOTIFY=false)\n")
        elif comparison_result.has_changes:
            notifier.send_change_notification(
                comparison_result, tracker_config.tenant_identifier,
                timestamp, tracker_config.github_run_url,
                release_intel=release_intel_by_module,
            )
            email_sent = True
        elif args.force_notify:
            notifier.send_forced_notification(
                new_snapshot["modules"], tracker_config.tenant_identifier,
                timestamp, tracker_config.github_run_url,
            )
            email_sent = True
        else:
            print("Email:\n  Notification: SKIPPED (no change)\n")

        if email_sent:
            print("Email:\n  Notification: SENT\n")
    except (ConfigError, NotificationError) as exc:
        email_failed = True
        print(f"Email:\n  Notification: FAILED ({exc})\n")

    # --- Public release announcement for UNCHANGED modules (spec 21-22) --
    # Qualys may announce a new version before any tenant has it -- tell
    # the user even though nothing in their tenant snapshot moved, but
    # only once per newly-discovered public version (dedup via
    # public_release_notifications.json), and only for modules not
    # already covered by the change email above.
    if release_config.check_public_releases and not is_first_run:
        if release_client is None:
            release_cache_data = release_cache.load_cache(paths["release_cache"])
            release_client = QualysReleaseNotesClient(release_config.index_url)
        notified_state = release_intelligence.load_notified_state(paths["public_release_state"])
        remaining_modules = {
            name: info
            for name, info in new_snapshot["modules"].items()
            if name not in changed_or_new_names
        }
        announcements = release_intelligence.check_public_release_announcements(
            release_client, release_cache_data, notified_state, remaining_modules,
            release_config.cache_ttl_days, timestamp,
        )
        if announcements:
            print(f"Public release announcements found: {len(announcements)}")
            if release_config.public_release_notification:
                try:
                    EmailNotifier(EmailConfig.from_env()).send_public_release_announcement(
                        announcements, tracker_config.tenant_identifier,
                        timestamp, tracker_config.github_run_url,
                    )
                    for intel in announcements:
                        notified_state[intel.module] = intel.latest_public_release.version
                    release_intelligence.save_notified_state(paths["public_release_state"], notified_state)
                    print("  Notification: SENT\n")
                except (ConfigError, NotificationError) as exc:
                    print(f"  Notification: FAILED ({exc})\n")
            else:
                print("  Notification: SKIPPED (PUBLIC_RELEASE_NOTIFICATION=false)\n")

    if release_cache_data is not None:
        release_cache.save_cache(paths["release_cache"], release_cache_data)

    stale_alert_sent = False
    duration = round(time.monotonic() - started_at, 2)

    record = {
        "timestamp": timestamp,
        "success": not email_failed,
        "api_success": True,
        "release_notes_lookup_success": release_notes_lookup_success,
        "changed": comparison_result.has_changes,
        "changed_modules": len(comparison_result.changed),
        "new_modules": len(comparison_result.new),
        "removed_modules": len(comparison_result.removed),
        "total_modules": comparison_result.total_current,
        "email_sent": email_sent,
        "stale_alert": stale_alert_sent,
        "duration_seconds": duration,
        "error": None if not email_failed else "Email notification failed",
    }
    run_log_module.append_run_record(
        paths["run_log"], record, tracker_config.run_log_max_entries
    )

    print("State:")
    print("  Snapshot: UPDATED")
    print("  History: UPDATED" if comparison_result.has_changes or is_first_run else "  History: UNCHANGED")
    print(f"\nCompleted {'successfully' if not email_failed else 'with errors'} in {duration} seconds.")
    print("=" * 60)

    return 1 if email_failed else 0


def _fail_run(
    paths: dict,
    tracker_config: TrackerConfig,
    timestamp: str,
    started_at: float,
    error: str,
) -> int:
    print(f"  Status: FAILED ({error})\n")
    print("API collection: FAILED")
    print("Snapshot NOT modified")
    print("No version-change email will be sent for this run")

    now = utcnow()
    staleness = check_staleness(
        [*run_log_module.load_run_log(paths["run_log"]), {"timestamp": timestamp, "api_success": False, "error": error}],
        now,
        tracker_config.stale_after_days,
        tracker_config.stale_alert_suppression_days,
    )

    stale_alert_sent = False
    if staleness.should_alert:
        try:
            email_config = EmailConfig.from_env()
            notifier = EmailNotifier(email_config)
            notifier.send_staleness_alert(
                tracker_config.tenant_identifier,
                staleness.last_success_at,
                error,
                tracker_config.stale_after_days,
                tracker_config.github_run_url,
            )
            stale_alert_sent = True
            print("Staleness alert: SENT")
        except (ConfigError, NotificationError) as exc:
            print(f"Staleness alert: FAILED to send ({exc})")

    duration = round(time.monotonic() - started_at, 2)
    record = {
        "timestamp": timestamp,
        "success": False,
        "api_success": False,
        "release_notes_lookup_success": None,
        "changed": False,
        "changed_modules": 0,
        "new_modules": 0,
        "removed_modules": 0,
        "total_modules": 0,
        "email_sent": False,
        "stale_alert": stale_alert_sent,
        "duration_seconds": duration,
        "error": error,
    }
    run_log_module.append_run_record(
        paths["run_log"], record, tracker_config.run_log_max_entries
    )

    print(f"\nRun FAILED after {duration} seconds.")
    print("=" * 60)
    return 1


def main() -> int:
    try:
        return run()
    except Exception:  # noqa: BLE001 -- top-level safety net, never crash silently
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
