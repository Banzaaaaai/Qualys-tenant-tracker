"""Email notifications.

Failures here are never swallowed: `send_*` methods raise
NotificationError on any SMTP failure so the caller (main.py) can
report it prominently and fail the job, per spec section 9.

Presentation follows the same visual language as the sibling
qualys-release-tracker project: a dark navy header bar, a light page
background and white rounded cards. Everything is table-based with
inline styles, because Outlook and Gmail strip <style> blocks.
"""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import EmailConfig
from .release_notes import full_product_name
from .models import ChangeType, ComparisonResult, ModuleReleaseIntelligence, UpgradeStatus

# --- Design tokens -------------------------------------------------------

FONT = "Arial,Helvetica,sans-serif"
PAGE_BG = "#f4f6f9"
CARD_BG = "#ffffff"
HEADER_BG = "#1a3a5c"
HEADER_TEXT = "#ffffff"
HEADER_SUBTEXT = "#adc8e6"
LINK_COLOR = "#1a5276"
BORDER_COLOR = "#e5e9ef"
RULE_COLOR = "#eeeeee"
TABLE_HEAD_BG = "#f0f4f8"
TEXT_COLOR = "#333333"
BODY_TEXT = "#444444"
MUTED_TEXT = "#888888"
RED = "#c0392b"
AMBER = "#d68910"
GREEN = "#27ae60"
CONTENT_WIDTH = 640

BODY_STYLE = (
    f"margin:0;padding:0;background:{PAGE_BG};font-family:{FONT};"
    f"font-size:15px;line-height:1.5;color:{TEXT_COLOR};"
)
CARD_STYLE = (
    f"width:100%;border-collapse:collapse;background:{CARD_BG};"
    f"border:1px solid {BORDER_COLOR};border-radius:6px;margin-bottom:14px;"
)
TABLE_STYLE = f"width:100%;border-collapse:collapse;font-family:{FONT};font-size:13px;"
TH_STYLE = (
    f"padding:8px 12px;text-align:left;color:{BODY_TEXT};font-weight:700;"
    f"background:{TABLE_HEAD_BG};border-bottom:2px solid #dde3ea;"
)
TD_STYLE = (
    f"padding:10px 12px;border-bottom:1px solid {RULE_COLOR};"
    f"vertical-align:top;color:{BODY_TEXT};"
)
P_STYLE = f"margin:10px 0 0;font-size:13px;color:{BODY_TEXT};line-height:1.6;"
META_STYLE = f"margin:0 0 16px;font-size:13px;color:{BODY_TEXT};line-height:1.8;"
SUBHEAD_STYLE = f"margin:16px 0 6px;font-size:13px;font-weight:700;color:{HEADER_BG};font-family:{FONT};"
SECTION_HEAD_STYLE = (
    f"margin:22px 0 12px;font-size:15px;font-weight:700;color:{HEADER_BG};font-family:{FONT};"
)
CARD_TITLE_STYLE = (
    f"margin:0;color:{LINK_COLOR};font-weight:700;font-size:16px;line-height:1.4;font-family:{FONT};"
)
DATE_STYLE = "color:#999999;font-size:12px;margin:6px 0 0;"
LIST_STYLE = f"margin:10px 0 0;padding-left:18px;font-size:13px;color:{BODY_TEXT};line-height:1.5;"
NOTICE_STYLE = (
    f"margin:0 0 16px;padding:12px 14px;background:#fdf6e3;border-left:4px solid {AMBER};"
    f"font-size:13px;color:{BODY_TEXT};line-height:1.6;"
)


class NotificationError(Exception):
    """Raised when an email could not be delivered. Never suppressed."""


def _badge(text: str, color: str) -> str:
    """A solid pill, as used for tags and status in the release tracker."""
    return (
        f'<span style="display:inline-block;padding:3px 9px;margin:2px 4px 2px 0;'
        f'border-radius:3px;background:{color};color:#ffffff;font-size:11px;'
        f'font-weight:700;font-family:{FONT};">{text}</span>'
    )


def _shell(title: str, subtitle: str, content: str) -> str:
    """Wrap `content` in the standard header bar + white card layout."""
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Qualys Tenant Version Tracker</title>
</head>
<body style="{BODY_STYLE}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background:{PAGE_BG};">
  <tr>
    <td align="center" style="padding:20px 10px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
             style="width:100%;max-width:{CONTENT_WIDTH}px;background:{CARD_BG};border-radius:6px;overflow:hidden;">
        <tr>
          <td style="background:{HEADER_BG};padding:24px 20px;">
            <h1 style="margin:0;color:{HEADER_TEXT};font-size:20px;line-height:1.3;font-family:{FONT};">{title}</h1>
            <p style="margin:8px 0 0;color:{HEADER_SUBTEXT};font-size:13px;font-family:{FONT};">{subtitle}</p>
          </td>
        </tr>
        <tr>
          <td style="padding:18px 16px;font-family:{FONT};">
            {content}
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


def _subtitle(tenant_identifier: str, timestamp: str) -> str:
    return f"{tenant_identifier} &nbsp;&middot;&nbsp; Checked at {timestamp}"


def _meta(pairs: list) -> str:
    inner = "<br>".join(f"<b>{label}:</b> {value}" for label, value in pairs)
    return f'<p style="{META_STYLE}">{inner}</p>'


def _row(cells: list[str]) -> str:
    tds = "".join(f'<td style="{TD_STYLE}">{c}</td>' for c in cells)
    return f"<tr>{tds}</tr>"


def _header_row(cells: list[str]) -> str:
    ths = "".join(f'<th style="{TH_STYLE}">{c}</th>' for c in cells)
    return f"<tr>{ths}</tr>"


def _table(headers: list[str], rows: list[str]) -> str:
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="{TABLE_STYLE}">'
        f"<thead>{_header_row(headers)}</thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _change_type_label(change_type: ChangeType) -> str:
    return {
        ChangeType.NEW_MODULE: "NEW_MODULE",
        ChangeType.VERSION_CHANGED: "VERSION_CHANGED",
        ChangeType.MODULE_REMOVED: "MODULE_REMOVED",
    }[change_type]


def _change_type_color(change_type: ChangeType) -> str:
    return {
        ChangeType.NEW_MODULE: GREEN,
        ChangeType.VERSION_CHANGED: HEADER_BG,
        ChangeType.MODULE_REMOVED: RED,
    }[change_type]


def _module_label(module: str) -> str:
    """Render a module as "TC (TotalCloud)" so the code is never bare.

    Falls back to the code alone when there is no curated product name
    for it -- an invented expansion would be worse than none.
    """
    full = full_product_name(module)
    return f"{module} ({full})" if full else module


def _build_change_table(comparison: ComparisonResult) -> str:
    rows = []
    for change in [*comparison.changed, *comparison.new, *comparison.removed]:
        rows.append(
            _row(
                [
                    f'<b style="color:{LINK_COLOR};">{_module_label(change.module)}</b>',
                    change.old_version or "&mdash;",
                    f"<b>{change.new_version}</b>" if change.new_version else "&mdash;",
                    _badge(
                        _change_type_label(change.change_type),
                        _change_type_color(change.change_type),
                    ),
                ]
            )
        )
    return _table(["Module", "Previous", "Current", "Change"], rows)


def _build_inventory_table(modules: dict[str, dict]) -> str:
    rows = []
    for module in sorted(modules):
        info = modules[module]
        rows.append(
            _row(
                [
                    f'<b style="color:{LINK_COLOR};">{_module_label(module)}</b>',
                    info.get("api_field", ""),
                    info.get("version", ""),
                    info.get("first_seen", ""),
                    info.get("last_changed", ""),
                ]
            )
        )
    return _table(["Module", "API Field", "Version", "First Seen", "Last Changed"], rows)


def _build_feature_list(features: list) -> str:
    if not features:
        return (
            f'<p style="{P_STYLE}"><i>No individual capabilities were listed on '
            "this release note.</i></p>"
        )
    items = "".join(
        f'<li style="margin-bottom:6px;"><strong>{f.title}</strong>'
        + (f" &mdash; {f.description}" if f.description else "")
        + "</li>"
        for f in features
    )
    return f'<ul style="{LIST_STYLE}">{items}</ul>'


def _release_link(release) -> str:
    if release is None or not release.url:
        return ""
    return (
        f'<p style="{P_STYLE}"><a href="{release.url}" '
        f'style="color:{LINK_COLOR};font-weight:700;text-decoration:none;">'
        "Official Qualys release notes &#8599;</a></p>"
    )


def _subhead(text: str) -> str:
    return f'<div style="{SUBHEAD_STYLE}">{text}</div>'


def _para(text: str) -> str:
    return f'<p style="{P_STYLE}">{text}</p>'


def _badge_line(text: str, color: str) -> str:
    return f'<p style="margin:8px 0 0;">{_badge(text, color)}</p>'


def _build_tenant_capabilities_block(module: str, intel: ModuleReleaseIntelligence) -> str:
    version = intel.tenant_version
    heading = _subhead(
        f"Capabilities available on your tenant now &mdash; {_module_label(module)} {version}"
    )
    if intel.tenant_release is not None:
        return (
            heading
            + _build_feature_list(intel.tenant_release.features)
            + _release_link(intel.tenant_release)
        )
    if intel.upgrade_status == UpgradeStatus.TENANT_AHEAD_OF_PUBLIC:
        latest = intel.latest_public_release
        return (
            heading
            + _badge_line(
                "CAPABILITIES AVAILABLE ON YOUR TENANT &mdash; NOT PUBLICLY ANNOUNCED YET", AMBER
            )
            + _para(
                f"Your tenant is running {version}, but Qualys has not published release "
                f"notes for it yet &mdash; the latest publicly announced version is "
                f"{latest.version}. The new capabilities in {version} cannot be listed "
                "until Qualys publishes them."
            )
        )
    return heading + _para("Official release notes: <i>Not found.</i>")


def _build_latest_public_block(module: str, intel: ModuleReleaseIntelligence) -> str:
    status = intel.upgrade_status

    if status == UpgradeStatus.RELEASE_NOTE_LOOKUP_FAILED:
        return (
            _subhead("Latest publicly announced version")
            + _badge_line("TEMPORARILY UNAVAILABLE", MUTED_TEXT)
            + _para(
                "Public release-note correlation: Temporarily unavailable. "
                "Your tenant version tracking was still successful."
            )
        )

    if status == UpgradeStatus.VERSION_ORDER_UNKNOWN:
        return (
            _subhead("Latest publicly announced version")
            + _badge_line("ORDERING UNKNOWN", MUTED_TEXT)
            + _para("Multiple public versions found; unable to safely determine ordering.")
        )

    if status == UpgradeStatus.PUBLIC_RELEASE_NOT_FOUND:
        return (
            _subhead("Latest publicly announced version")
            + _badge_line("NOT FOUND", MUTED_TEXT)
            + _para("Release notes: Not found.")
        )

    latest = intel.latest_public_release
    released = _para(f"<b>Released:</b> {latest.release_date}") if latest.release_date else ""

    if status == UpgradeStatus.TENANT_AHEAD_OF_PUBLIC:
        return (
            _subhead(
                f"Latest publicly announced version &mdash; {_module_label(module)} {latest.version}"
            )
            + released
            + _para(
                f"Your tenant version ({intel.tenant_version}) is ahead of the latest "
                f"publicly announced version ({latest.version}). Qualys may publish the "
                "release notes for your tenant version later."
            )
            + _release_link(latest)
        )

    if status == UpgradeStatus.CURRENT:
        return (
            _subhead(
                f"Latest publicly announced version &mdash; {_module_label(module)} {latest.version}"
            )
            + _badge_line("CURRENT", GREEN)
            + _para("Your tenant is already running the latest publicly announced version.")
            + released
            + _release_link(latest)
        )

    # PUBLIC_NEWER_VERSION_AVAILABLE
    return (
        _subhead(
            f"Latest publicly announced version &mdash; {_module_label(module)} {latest.version}"
        )
        + released
        + _badge_line("PUBLICLY ANNOUNCED &mdash; NOT YET DETECTED ON YOUR TENANT", AMBER)
        + _para(
            f"Qualys has publicly announced version {latest.version}, but version "
            f"{latest.version} has not yet been detected on your tenant "
            f"(your tenant currently reports {intel.tenant_version}). Qualys may perform "
            "phased tenant rollouts, so this does not necessarily indicate a problem."
        )
        + _release_link(latest)
        + _para("New capabilities in the announced version:")
        + _build_feature_list(latest.features)
    )


def _build_module_intelligence_block(
    index: int, module: str, intel: ModuleReleaseIntelligence
) -> str:
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="{CARD_STYLE}">'
        f'<tr><td style="padding:16px;font-family:{FONT};">'
        f'<p style="{CARD_TITLE_STYLE}">{index}. {_module_label(module)}</p>'
        f'<p style="{DATE_STYLE}">Your tenant version {intel.tenant_version}</p>'
        f"{_build_tenant_capabilities_block(module, intel)}"
        f"{_build_latest_public_block(module, intel)}"
        f"</td></tr></table>"
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

    return (
        f'<div style="{SECTION_HEAD_STYLE}">Capabilities &amp; release intelligence</div>'
        + "".join(blocks)
    )


def _footer(run_url: str | None) -> str:
    """No email footer: the "View this run" link and the product
    sign-off line were dropped at the user's request. `run_url` is
    still accepted (and still recorded in the run log) so callers and
    config need no change if a footer is ever wanted again.
    """
    return ""


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

        plural = "s" if count != 1 else ""
        body = _shell(
            f"&#128276; Qualys Tenant Version Change Detected &mdash; {count} Module{plural}",
            _subtitle(tenant_identifier, timestamp),
            f"{_build_change_table(comparison)}"
            f"{_build_release_intelligence_section(comparison, release_intel)}"
            f"{_footer(run_url)}",
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
                "publicly announced (not yet on your tenant)"
            )
        else:
            subject = (
                f"[Qualys Tenant] {len(announcements)} new public releases announced "
                "(not yet on your tenant)"
            )

        blocks = "".join(
            _build_module_intelligence_block(i, intel.module, intel)
            for i, intel in enumerate(announcements, start=1)
        )
        body = _shell(
            "&#128276; Qualys Public Release Announcement",
            _subtitle(tenant_identifier, timestamp),
            f'<p style="{NOTICE_STYLE}">Your tenant\'s reported version has not changed, '
            "but Qualys has publicly announced a newer version for the module(s) below. "
            "This does not necessarily mean your tenant is overdue &mdash; Qualys may "
            "perform phased rollouts across tenants.</p>"
            f"{blocks}"
            f"{_footer(run_url)}",
        )
        self._send(subject, body)

    def send_initial_baseline_notification(
        self, modules: dict[str, dict], tenant_identifier: str, timestamp: str, run_url: str | None
    ) -> None:
        subject = f"[Qualys Tenant] Initial baseline created ({len(modules)} modules)"
        body = _shell(
            "Qualys Tenant Version Tracker &mdash; Initial Baseline",
            _subtitle(tenant_identifier, timestamp),
            _meta([("Total modules", len(modules))])
            + f'<p style="{NOTICE_STYLE}">This is the first run. No prior snapshot existed, '
            "so the versions below are being saved as the baseline. Future runs will only "
            "notify when something actually changes.</p>"
            + f"{_build_inventory_table(modules)}"
            + f"{_footer(run_url)}",
        )
        self._send(subject, body)

    def send_forced_notification(
        self, modules: dict[str, dict], tenant_identifier: str, timestamp: str, run_url: str | None
    ) -> None:
        subject = f"[Qualys Tenant] Manual / forced notification ({len(modules)} modules)"
        body = _shell(
            "Qualys Tenant Version Tracker &mdash; Current Inventory",
            _subtitle(tenant_identifier, timestamp),
            f'<p style="{NOTICE_STYLE}"><b>Manual / forced notification</b> &mdash; '
            "requested manually; this is not an upgrade notification.</p>"
            + _meta([("Total modules", len(modules))])
            + f"{_build_inventory_table(modules)}"
            + f"{_footer(run_url)}",
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
        body = _shell(
            "&#128993; Qualys Tenant Version Tracker &mdash; Staleness Alert",
            tenant_identifier,
            f'<p style="{NOTICE_STYLE}">The tracker has not successfully retrieved your tenant '
            f"version information for more than {stale_after_days} day(s).</p>"
            + _meta(
                [
                    ("Last successful check", last_success_at or "never"),
                    ("Last error", last_error or "unknown"),
                ]
            )
            + f"{_footer(run_url)}",
        )
        self._send(subject, body)

    def send_test_email(self) -> None:
        body = _shell(
            "Qualys Tenant Version Tracker &mdash; Test Email",
            "SMTP delivery check",
            _para(
                "This is a test message confirming SMTP delivery is configured "
                "correctly. No tenant data is included."
            ),
        )
        self._send("[Qualys Tenant] Test email", body)

    def _send(self, subject: str, html_body: str, message_id: str | None = None) -> None:
        message = MIMEMultipart("alternative")
        if message_id:
            message["Message-ID"] = f"<{message_id}@qualys-tracker.local>"
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
                refused = server.sendmail(
                    self._config.email_from, self._config.email_to, message.as_string()
                )
                if refused:
                    raise NotificationError("SMTP refused one or more recipients; delivery will be retried")
        except (smtplib.SMTPException, OSError) as exc:
            # Never include SMTP credentials in the raised message.
            raise NotificationError(f"Failed to send email: {type(exc).__name__}") from exc
