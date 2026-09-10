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
from .models import ChangeType, ComparisonResult, ModuleReleaseIntelligence, UpgradeStatus

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


CAPABILITY_BOX_STYLE = (
    "border:1px solid #d0d7de;border-radius:6px;padding:12px 16px;margin:10px 0;"
)
WARNING_BADGE_STYLE = (
    "display:inline-block;background:#fff2cc;color:#7a5b00;border:1px solid #d4b106;"
    "border-radius:4px;padding:2px 8px;font-weight:bold;font-size:12px;"
)
OK_BADGE_STYLE = (
    "display:inline-block;background:#e6f4ea;color:#1e7a34;border:1px solid #7bc492;"
    "border-radius:4px;padding:2px 8px;font-weight:bold;font-size:12px;"
)
NEUTRAL_BADGE_STYLE = (
    "display:inline-block;background:#eef1f4;color:#57606a;border:1px solid #d0d7de;"
    "border-radius:4px;padding:2px 8px;font-weight:bold;font-size:12px;"
)


def _build_feature_list(features: list) -> str:
    if not features:
        return "<p><i>No individual capabilities were listed on this release note.</i></p>"
    items = "".join(
        f"<li><b>{f.title}</b>"
        + (f" &mdash; {f.description}" if f.description else "")
        + "</li>"
        for f in features
    )
    return f'<ul style="margin:4px 0;padding-left:20px;">{items}</ul>'


def _release_link(release) -> str:
    if release is None or not release.url:
        return ""
    return f'<p><a href="{release.url}">Official Qualys release notes</a></p>'


def _build_tenant_capabilities_block(module: str, intel: ModuleReleaseIntelligence) -> str:
    version = intel.tenant_version
    if intel.tenant_release is not None:
        body = (
            f"<h4>Capabilities available on this tenant now &mdash; {module} {version}</h4>"
            f"{_build_feature_list(intel.tenant_release.features)}"
            f"{_release_link(intel.tenant_release)}"
        )
    else:
        body = (
            f"<h4>Capabilities available on this tenant now &mdash; {module} {version}</h4>"
            f"<p>Official release notes: <i>Not found.</i></p>"
        )
    return body


def _build_latest_public_block(module: str, intel: ModuleReleaseIntelligence) -> str:
    status = intel.upgrade_status

    if status == UpgradeStatus.RELEASE_NOTE_LOOKUP_FAILED:
        return (
            "<h4>Latest publicly announced version</h4>"
            f'<p><span style="{NEUTRAL_BADGE_STYLE}">TEMPORARILY UNAVAILABLE</span></p>'
            "<p>Public release-note correlation: Temporarily unavailable. "
            "Tenant version tracking was still successful.</p>"
        )

    if status == UpgradeStatus.VERSION_ORDER_UNKNOWN:
        return (
            "<h4>Latest publicly announced version</h4>"
            f'<p><span style="{NEUTRAL_BADGE_STYLE}">ORDERING UNKNOWN</span></p>'
            "<p>Multiple public versions found; unable to safely determine ordering.</p>"
        )

    if status == UpgradeStatus.PUBLIC_RELEASE_NOT_FOUND:
        return (
            "<h4>Latest publicly announced version</h4>"
            f'<p><span style="{NEUTRAL_BADGE_STYLE}">NOT FOUND</span></p>'
            "<p>Release notes: Not found.</p>"
        )

    latest = intel.latest_public_release

    if status == UpgradeStatus.CURRENT:
        return (
            f"<h4>Latest publicly announced version &mdash; {module} {latest.version}</h4>"
            f'<p><span style="{OK_BADGE_STYLE}">CURRENT</span></p>'
            f"<p>This tenant is already running the latest publicly announced version.</p>"
            + (f"<p><b>Released:</b> {latest.release_date}</p>" if latest.release_date else "")
            + _release_link(latest)
        )

    # PUBLIC_NEWER_VERSION_AVAILABLE
    return (
        f"<h4>Latest publicly announced version &mdash; {module} {latest.version}</h4>"
        + (f"<p><b>Released:</b> {latest.release_date}</p>" if latest.release_date else "")
        + f'<p><span style="{WARNING_BADGE_STYLE}">PUBLICLY ANNOUNCED &mdash; NOT YET DETECTED ON THIS TENANT</span></p>'
        + f"<p>Qualys has publicly announced version {latest.version}, but version "
        + f"{latest.version} has not yet been detected on this tenant "
        + f"(tenant currently reports {intel.tenant_version}). Qualys may perform "
        + "phased tenant rollouts, so this does not necessarily indicate a problem.</p>"
        + "<p>New capabilities in the announced version:</p>"
        + _build_feature_list(latest.features)
        + _release_link(latest)
    )


def _build_module_intelligence_block(index: int, module: str, intel: ModuleReleaseIntelligence) -> str:
    return (
        f'<div style="{CAPABILITY_BOX_STYLE}">'
        f"<h3>{index}. {module}</h3>"
        f"{_build_tenant_capabilities_block(module, intel)}"
        f"{_build_latest_public_block(module, intel)}"
        f"</div>"
    )


def _build_release_intelligence_section(
    comparison: ComparisonResult,
    release_intel: dict[str, ModuleReleaseIntelligence] | None,
) -> str:
    """Render the "capabilities & release intelligence" section.

    `release_intel=None` means correlation was never attempted for this
    email (the section is omitted entirely, silently -- e.g. a caller
    that doesn't use this feature at all). A dict -- even an empty one,
    or one missing a specific changed module -- means correlation WAS
    attempted this run; any changed/new module absent from it is
    rendered as "temporarily unavailable" for that module specifically
    (spec section 18: a lookup failure must never suppress the
    underlying tenant-change information already shown above).
    """
    eligible = [c for c in [*comparison.changed, *comparison.new]]
    if not eligible or release_intel is None:
        return ""

    blocks = []
    for i, change in enumerate(eligible, start=1):
        intel = release_intel.get(change.module)
        if intel is None:
            intel = ModuleReleaseIntelligence(
                module=change.module,
                tenant_version=change.new_version or "",
                tenant_release=None,
                latest_public_release=None,
                upgrade_status=UpgradeStatus.RELEASE_NOTE_LOOKUP_FAILED,
            )
        blocks.append(_build_module_intelligence_block(i, change.module, intel))

    return "<h3>Capabilities &amp; release intelligence</h3>" + "".join(blocks)


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
        release_intel: dict[str, ModuleReleaseIntelligence] | None = None,
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
            f"{_build_release_intelligence_section(comparison, release_intel)}"
            f"{_footer(run_url)}"
        )
        self._send(subject, body)

    def send_public_release_announcement(
        self,
        announcements: list[ModuleReleaseIntelligence],
        tenant_identifier: str,
        timestamp: str,
        run_url: str | None,
    ) -> None:
        """Section 21: Qualys announced a new public version for a module
        whose tenant version has NOT changed. Distinct from
        send_change_notification -- no tenant version change occurred."""
        if len(announcements) == 1:
            intel = announcements[0]
            subject = (
                f"[Qualys Tenant] {intel.module} {intel.latest_public_release.version} "
                "publicly announced (not yet on this tenant)"
            )
        else:
            subject = (
                f"[Qualys Tenant] {len(announcements)} new public releases announced "
                "(not yet on this tenant)"
            )

        blocks = "".join(
            _build_module_intelligence_block(i, intel.module, intel)
            for i, intel in enumerate(announcements, start=1)
        )
        body = _wrap(
            "<h2>Qualys Public Release Announcement</h2>"
            f"<p><b>Tenant:</b> {tenant_identifier}<br>"
            f"<b>Checked at:</b> {timestamp}</p>"
            "<p>Your tenant's reported version has not changed, but Qualys has "
            "publicly announced a newer version for the module(s) below. This "
            "does not necessarily mean the tenant is overdue -- Qualys may "
            "perform phased rollouts across tenants.</p>"
            f"{blocks}"
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
