import smtplib

import pytest

from qualys_tracker.comparison import compare
from qualys_tracker.config import EmailConfig
from qualys_tracker.models import ModuleVersion
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
