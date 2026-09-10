import smtplib

import pytest

from qualys_tracker.comparison import compare
from qualys_tracker.config import EmailConfig
from qualys_tracker.models import (
    Feature,
    ModuleReleaseIntelligence,
    ModuleVersion,
    ReleaseInfo,
    UpgradeStatus,
)
from qualys_tracker.notifier import EmailNotifier, NotificationError


class FakeSMTP:
    sent = []
    fail = False

    def __init__(self, host, port, timeout=None):
        FakeSMTP.host, FakeSMTP.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, username, password):
        pass

    def sendmail(self, from_addr, to_addrs, message):
        if FakeSMTP.fail:
            raise smtplib.SMTPException("boom")
        FakeSMTP.sent.append((from_addr, to_addrs, message))


@pytest.fixture
def email_config():
    return EmailConfig(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user",
        smtp_password="pass",
        email_from="tracker@example.com",
        email_to=["ops@example.com"],
        use_tls=True,
    )


@pytest.fixture(autouse=True)
def patch_smtp(monkeypatch):
    FakeSMTP.sent = []
    FakeSMTP.fail = False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    yield


def test_one_changed_module_email(email_config):
    previous = {"FIM": {"version": "1.0.0"}}
    current = [ModuleVersion("FIM-VERSION", "FIM", "1.0.1")]
    result = compare(previous, current)

    notifier = EmailNotifier(email_config)
    notifier.send_change_notification(result, "tenant-1", "2026-01-01T00:00:00Z", None)

    assert len(FakeSMTP.sent) == 1
    _, _, message = FakeSMTP.sent[0]
    assert "upgraded" in message
    assert "1.0.0" in message and "1.0.1" in message


def test_multiple_changed_modules_subject(email_config):
    previous = {"FIM": {"version": "1.0.0"}, "WAF": {"version": "2.0.0"}}
    current = [
        ModuleVersion("FIM-VERSION", "FIM", "1.0.1"),
        ModuleVersion("WAF-VERSION", "WAF", "2.0.1"),
    ]
    result = compare(previous, current)

    notifier = EmailNotifier(email_config)
    notifier.send_change_notification(result, "tenant-1", "2026-01-01T00:00:00Z", None)

    _, _, message = FakeSMTP.sent[0]
    assert "2 module versions changed" in message


def test_initial_run_email_is_labeled_baseline(email_config):
    notifier = EmailNotifier(email_config)
    notifier.send_initial_baseline_notification(
        {"FIM": {"api_field": "FIM-VERSION", "version": "1.0.0",
                  "first_seen": "x", "last_changed": "x"}},
        "tenant-1", "2026-01-01T00:00:00Z", None,
    )
    _, _, message = FakeSMTP.sent[0]
    assert "baseline" in message.lower()


def test_forced_notification_is_clearly_labeled(email_config):
    notifier = EmailNotifier(email_config)
    notifier.send_forced_notification(
        {"FIM": {"api_field": "FIM-VERSION", "version": "1.0.0",
                  "first_seen": "x", "last_changed": "x"}},
        "tenant-1", "2026-01-01T00:00:00Z", None,
    )
    _, _, message = FakeSMTP.sent[0]
    assert "Manual / forced notification" in message


def test_no_changes_means_no_call_needed():
    previous = {"FIM": {"version": "1.0.0"}}
    current = [ModuleVersion("FIM-VERSION", "FIM", "1.0.0")]
    result = compare(previous, current)
    assert not result.has_changes  # caller in main.py simply skips sending


def test_email_failure_raises_notification_error(email_config):
    FakeSMTP.fail = True
    notifier = EmailNotifier(email_config)
    with pytest.raises(NotificationError):
        notifier.send_test_email()


# --- Release-intelligence enhanced sections ----------------------------


def _intel(module, tenant_version, status, latest_version=None, features=None, tenant_found=True):
    tenant_release = (
        ReleaseInfo(version=tenant_version, url=f"https://docs.qualys.com/{module}/{tenant_version}.htm",
                    release_date="August 12, 2026", features=[Feature("Old capability", "desc")])
        if tenant_found else None
    )
    latest_release = None
    if latest_version is not None:
        latest_release = ReleaseInfo(
            version=latest_version, url=f"https://docs.qualys.com/{module}/{latest_version}.htm",
            release_date="September 09, 2026", features=features or [],
        )
    return ModuleReleaseIntelligence(
        module=module, tenant_version=tenant_version, tenant_release=tenant_release,
        latest_public_release=latest_release, upgrade_status=status,
    )


def test_change_notification_with_newer_public_version(email_config):
    previous = {"FIM": {"version": "4.9.3"}}
    current = [ModuleVersion("FIM-VERSION", "FIM", "4.9.3")]
    result = compare(previous, current)
    # Force a VERSION_CHANGED-shaped result for rendering purposes
    from qualys_tracker.models import ChangeType, ModuleChange
    result.changed = [ModuleChange("FIM", "FIM-VERSION", "4.9.3", "4.9.4", ChangeType.VERSION_CHANGED)]
    result.unchanged = []

    intel = _intel(
        "FIM", "4.9.4", UpgradeStatus.PUBLIC_NEWER_VERSION_AVAILABLE,
        latest_version="4.9.5", features=[Feature("New capability X", "does X")],
    )

    notifier = EmailNotifier(email_config)
    notifier.send_change_notification(
        result, "tenant-1", "2026-01-01T00:00:00Z", None, release_intel={"FIM": intel}
    )

    _, _, message = FakeSMTP.sent[0]
    assert "NOT YET DETECTED ON THIS TENANT" in message
    assert "New capability X" in message
    assert "has not yet been detected on this tenant" in message
    assert "behind" not in message.lower()  # spec section 15: precise wording only


def test_change_notification_current_status(email_config):
    from qualys_tracker.models import ChangeType, ModuleChange

    result = compare({"WAS": {"version": "6.0.0"}}, [ModuleVersion("WAS-VERSION", "WAS", "6.0.1")])
    result.changed = [ModuleChange("WAS", "WAS-VERSION", "6.0.0", "6.0.1", ChangeType.VERSION_CHANGED)]
    result.unchanged = []

    intel = _intel("WAS", "6.0.1", UpgradeStatus.CURRENT, latest_version="6.0.1")

    notifier = EmailNotifier(email_config)
    notifier.send_change_notification(
        result, "tenant-1", "2026-01-01T00:00:00Z", None, release_intel={"WAS": intel}
    )
    _, _, message = FakeSMTP.sent[0]
    assert "CURRENT" in message
    assert "already running the latest publicly announced version" in message


def test_change_notification_release_lookup_failed_does_not_break_email(email_config):
    from qualys_tracker.models import ChangeType, ModuleChange

    result = compare({"FIM": {"version": "4.9.3"}}, [ModuleVersion("FIM-VERSION", "FIM", "4.9.4")])
    result.changed = [ModuleChange("FIM", "FIM-VERSION", "4.9.3", "4.9.4", ChangeType.VERSION_CHANGED)]
    result.unchanged = []

    intel = ModuleReleaseIntelligence(
        module="FIM", tenant_version="4.9.4", tenant_release=None,
        latest_public_release=None, upgrade_status=UpgradeStatus.RELEASE_NOTE_LOOKUP_FAILED,
        error="network error",
    )

    notifier = EmailNotifier(email_config)
    notifier.send_change_notification(
        result, "tenant-1", "2026-01-01T00:00:00Z", None, release_intel={"FIM": intel}
    )
    assert len(FakeSMTP.sent) == 1  # tenant change email still sent
    _, _, message = FakeSMTP.sent[0]
    assert "Temporarily unavailable" in message


def test_change_notification_without_release_intel_preserves_existing_behavior(email_config):
    # release_intel omitted entirely -- e.g. a caller that predates this
    # feature -- must behave exactly like before: no correlation section
    # at all, existing tenant-change email unaffected.
    result = compare({"FIM": {"version": "1.0.0"}}, [ModuleVersion("FIM-VERSION", "FIM", "1.0.1")])
    notifier = EmailNotifier(email_config)
    notifier.send_change_notification(result, "tenant-1", "2026-01-01T00:00:00Z", None)
    _, _, message = FakeSMTP.sent[0]
    assert "1.0.0" in message and "1.0.1" in message
    assert "Capabilities &amp; release intelligence" not in message
    assert "Temporarily unavailable" not in message


def test_change_notification_module_missing_from_intel_dict_shown_as_unavailable(email_config):
    # release_intel WAS attempted this run (a dict, even if a module in it
    # is missing) -- that specific module's block must say so, without
    # dropping the underlying version-change information.
    from qualys_tracker.models import ChangeType, ModuleChange

    result = compare({"FIM": {"version": "4.9.3"}}, [ModuleVersion("FIM-VERSION", "FIM", "4.9.4")])
    result.changed = [ModuleChange("FIM", "FIM-VERSION", "4.9.3", "4.9.4", ChangeType.VERSION_CHANGED)]
    result.unchanged = []

    notifier = EmailNotifier(email_config)
    notifier.send_change_notification(
        result, "tenant-1", "2026-01-01T00:00:00Z", None, release_intel={}
    )
    _, _, message = FakeSMTP.sent[0]
    assert "4.9.3" in message and "4.9.4" in message
    assert "Temporarily unavailable" in message


def test_multiple_modules_consolidated_into_one_email(email_config):
    from qualys_tracker.models import ChangeType, ModuleChange

    result = compare(
        {"FIM": {"version": "4.9.3"}, "WAS": {"version": "6.0.0"}},
        [ModuleVersion("FIM-VERSION", "FIM", "4.9.4"), ModuleVersion("WAS-VERSION", "WAS", "6.0.1")],
    )
    result.changed = [
        ModuleChange("FIM", "FIM-VERSION", "4.9.3", "4.9.4", ChangeType.VERSION_CHANGED),
        ModuleChange("WAS", "WAS-VERSION", "6.0.0", "6.0.1", ChangeType.VERSION_CHANGED),
    ]
    result.unchanged = []

    release_intel = {
        "FIM": _intel("FIM", "4.9.4", UpgradeStatus.CURRENT, latest_version="4.9.4"),
        "WAS": _intel("WAS", "6.0.1", UpgradeStatus.PUBLIC_NEWER_VERSION_AVAILABLE, latest_version="6.0.2"),
    }

    notifier = EmailNotifier(email_config)
    notifier.send_change_notification(
        result, "tenant-1", "2026-01-01T00:00:00Z", None, release_intel=release_intel
    )

    assert len(FakeSMTP.sent) == 1  # one consolidated email, not two
    _, _, message = FakeSMTP.sent[0]
    assert "1. FIM" in message
    assert "2. WAS" in message


def test_public_release_announcement_labeled_distinctly_from_change(email_config):
    intel = _intel(
        "FIM", "4.9.3", UpgradeStatus.PUBLIC_NEWER_VERSION_AVAILABLE,
        latest_version="4.9.4", tenant_found=False,
    )
    notifier = EmailNotifier(email_config)
    notifier.send_public_release_announcement([intel], "tenant-1", "2026-01-01T00:00:00Z", None)

    assert len(FakeSMTP.sent) == 1
    _, _, message = FakeSMTP.sent[0]
    assert "publicly announced" in message.lower()
    assert "Qualys Public Release Announcement" in message
