"""Email notifications.

Failures here are never swallowed: `send_*` methods raise
NotificationError on any SMTP failure so the caller (main.py) can
report it prominently and fail the job, per spec section 9.
"""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import EmailConfig
from .models import ChangeType, ComparisonResult

TABLE_STYLE = (
    "border-collapse:collapse;width:100%;font-family:Arial,Helvetica,sans-serif;"
    "font-size:14px;"
)
TH_STYLE = (
    "text-align:left;padding:8px 12px;background-color:#0b3d63;color:#ffffff;"
    "border:1px solid #0b3d63;"
)
TD_STYLE = "padding:8px 12px;border:1px solid #d0d7de;"
WRAP_STYLE = "font-family:Arial,Helvetica,sans-serif;color:#1a1a1a;font-size:14px;"


class NotificationError(Exception):
    """Raised when an email could not be delivered. Never suppressed."""


def _row(cells: list[str]) -> str:
    tds = "".join(f'<td style="{TD_STYLE}">{c}</td>' for c in cells)
    return f"<tr>{tds}</tr>"


def _change_type_label(change_type: ChangeType) -> str:
    return {
        ChangeType.NEW_MODULE: "NEW_MODULE",
        ChangeType.VERSION_CHANGED: "VERSION_CHANGED",
        ChangeType.MODULE_REMOVED: "MODULE_REMOVED",
    }[change_type]


def _build_change_table(comparison: ComparisonResult) -> str:
    rows = []
    for change in [*comparison.changed, *comparison.new, *comparison.removed]:
        rows.append(
            _row(
                [
                    change.module,
                    change.old_version or "&mdash;",
                    change.new_version or "&mdash;",
                    _change_type_label(change.change_type),
                ]
            )
        )
    header = _row(["Module", "Previous", "Current", "Change"])
    header = header.replace('<td style="' + TD_STYLE + '">', f'<th style="{TH_STYLE}">').replace(
        "</td>", "</th>"
    )
    return f'<table style="{TABLE_STYLE}"><thead>{header}</thead><tbody>{"".join(rows)}</tbody></table>'


def _build_inventory_table(modules: dict[str, dict]) -> str:
    rows = []
    for module in sorted(modules):
        info = modules[module]
        rows.append(
            _row(
                [
                    module,
                    info.get("api_field", ""),
                    info.get("version", ""),
                    info.get("first_seen", ""),
                    info.get("last_changed", ""),
                ]
            )
        )
    header = _row(["Module", "API Field", "Version", "First Seen", "Last Changed"])
    header = header.replace('<td style="' + TD_STYLE + '">', f'<th style="{TH_STYLE}">').replace(
        "</td>", "</th>"
    )
    return f'<table style="{TABLE_STYLE}"><thead>{header}</thead><tbody>{"".join(rows)}</tbody></table>'


def _footer(run_url: str | None) -> str:
    link = f'<p><a href="{run_url}">View this run</a></p>' if run_url else ""
    return f'<hr style="border:none;border-top:1px solid #d0d7de;margin:20px 0;">{link}' \
        '<p style="color:#57606a;font-size:12px;">Qualys Tenant Version Tracker</p>'


def _wrap(body: str) -> str:
    return f'<div style="{WRAP_STYLE}">{body}</div>'


class EmailNotifier:
    def __init__(self, config: EmailConfig) -> None:
        self._config = config

    def send_change_notification(
        self,
        comparison: ComparisonResult,
        tenant_identifier: str,
        timestamp: str,
        run_url: str | None,
    ) -> None:
        count = comparison.changed_count
        if count == 1 and len(comparison.changed) == 1:
            change = comparison.changed[0]
            subject = (
                f"[Qualys Tenant] {change.module} upgraded "
                f"{change.old_version} -> {change.new_version}"
            )
        else:
            subject = f"[Qualys Tenant] {count} module versions changed"

        body = _wrap(
            f"<h2>Qualys Tenant Version Change Detected</h2>"
            f"<p><b>Tenant:</b> {tenant_identifier}<br>"
            f"<b>Checked at:</b> {timestamp}<br>"
            f"<b>Modules changed:</b> {count}</p>"
            f"{_build_change_table(comparison)}"
            f"<p>Unchanged modules: {len(comparison.unchanged)}<br>"
            f"Changed modules: {len(comparison.changed)}<br>"
            f"New modules: {len(comparison.new)}<br>"
            f"Removed modules: {len(comparison.removed)}</p>"
            f"{_footer(run_url)}"
        )
        self._send(subject, body)

    def send_initial_baseline_notification(
        self, modules: dict[str, dict], tenant_identifier: str, timestamp: str, run_url: str | None
    ) -> None:
        subject = f"[Qualys Tenant] Initial baseline created ({len(modules)} modules)"
        body = _wrap(
            f"<h2>Qualys Tenant Version Tracker &mdash; Initial Baseline</h2>"
            f"<p><b>Tenant:</b> {tenant_identifier}<br>"
            f"<b>Checked at:</b> {timestamp}<br>"
            f"<b>Total modules:</b> {len(modules)}</p>"
            f"<p>This is the first run. No prior snapshot existed, so the "
            f"versions below are being saved as the baseline. Future runs "
            f"will only notify when something actually changes.</p>"
            f"{_build_inventory_table(modules)}"
            f"{_footer(run_url)}"
        )
        self._send(subject, body)

    def send_forced_notification(
        self, modules: dict[str, dict], tenant_identifier: str, timestamp: str, run_url: str | None
    ) -> None:
        subject = f"[Qualys Tenant] Manual / forced notification ({len(modules)} modules)"
        body = _wrap(
            f'<p style="background:#fff8c5;border:1px solid #d4c65a;padding:8px 12px;">'
            f"<b>Manual / forced notification</b> &mdash; requested manually; "
            f"this is not an upgrade notification.</p>"
            f"<h2>Qualys Tenant Version Tracker &mdash; Current Inventory</h2>"
            f"<p><b>Tenant:</b> {tenant_identifier}<br>"
            f"<b>Checked at:</b> {timestamp}<br>"
            f"<b>Total modules:</b> {len(modules)}</p>"
            f"{_build_inventory_table(modules)}"
            f"{_footer(run_url)}"
        )
        self._send(subject, body)

    def send_staleness_alert(
        self,
        tenant_identifier: str,
        last_success_at: str | None,
        last_error: str | None,
        stale_after_days: int,
        run_url: str | None,
    ) -> None:
        subject = f"[Qualys Tenant] Tracker has not succeeded in over {stale_after_days} day(s)"
        body = _wrap(
            f"<h2>Qualys Tenant Version Tracker &mdash; Staleness Alert</h2>"
            f"<p><b>Tenant:</b> {tenant_identifier}</p>"
            f"<p>The tracker has not successfully retrieved tenant version "
            f"information for more than {stale_after_days} day(s).</p>"
            f"<p><b>Last successful check:</b> {last_success_at or 'never'}<br>"
            f"<b>Last error:</b> {last_error or 'unknown'}</p>"
            f"{_footer(run_url)}"
        )
        self._send(subject, body)

    def send_test_email(self) -> None:
        body = _wrap(
            "<h2>Qualys Tenant Version Tracker &mdash; Test Email</h2>"
            "<p>This is a test message confirming SMTP delivery is configured "
            "correctly. No tenant data is included.</p>"
        )
        self._send("[Qualys Tenant] Test email", body)

    def _send(self, subject: str, html_body: str) -> None:
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = self._config.email_from
        message["To"] = ", ".join(self._config.email_to)
        message.attach(MIMEText(html_body, "html"))

        try:
            with smtplib.SMTP(
                self._config.smtp_host, self._config.smtp_port, timeout=30
            ) as server:
                if self._config.use_tls:
                    server.starttls()
                if self._config.smtp_username:
                    server.login(self._config.smtp_username, self._config.smtp_password)
                server.sendmail(
                    self._config.email_from, self._config.email_to, message.as_string()
                )
        except (smtplib.SMTPException, OSError) as exc:
            # Never include SMTP credentials in the raised message.
            raise NotificationError(f"Failed to send email: {type(exc).__name__}") from exc
