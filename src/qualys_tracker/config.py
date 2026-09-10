"""Configuration loaded exclusively from environment variables.

No credential ever has a default value baked into source code, and
nothing here is ever logged verbatim (see main.py's console output,
which never prints these fields).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class QualysConfig:
    api_url: str
    username: str
    password: str
    timeout_seconds: int = 30
    max_retries: int = 5

    @classmethod
    def from_env(cls) -> "QualysConfig":
        api_url = os.environ.get("QUALYS_API_URL", "").strip()
        username = os.environ.get("QUALYS_USERNAME", "").strip()
        password = os.environ.get("QUALYS_PASSWORD", "")
        if not api_url:
            raise ConfigError("QUALYS_API_URL is required")
        if not username:
            raise ConfigError("QUALYS_USERNAME is required")
        if not password:
            raise ConfigError("QUALYS_PASSWORD is required")
        return cls(
            api_url=api_url.rstrip("/"),
            username=username,
            password=password,
            timeout_seconds=_env_int("QUALYS_HTTP_TIMEOUT_SECONDS", 30),
            max_retries=_env_int("QUALYS_HTTP_MAX_RETRIES", 5),
        )


@dataclass
class EmailConfig:
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    email_from: str
    email_to: list[str]
    use_tls: bool = True

    @classmethod
    def from_env(cls) -> "EmailConfig":
        smtp_host = os.environ.get("SMTP_HOST", "").strip()
        email_from = os.environ.get("EMAIL_FROM", "").strip()
        email_to = _env_list("EMAIL_TO")
        if not smtp_host:
            raise ConfigError("SMTP_HOST is required")
        if not email_from:
            raise ConfigError("EMAIL_FROM is required")
        if not email_to:
            raise ConfigError("EMAIL_TO is required (comma-separated list)")
        return cls(
            smtp_host=smtp_host,
            smtp_port=_env_int("SMTP_PORT", 587),
            smtp_username=os.environ.get("SMTP_USERNAME", ""),
            smtp_password=os.environ.get("SMTP_PASSWORD", ""),
            email_from=email_from,
            email_to=email_to,
            use_tls=_env_bool("SMTP_USE_TLS", True),
        )


@dataclass
class TrackerConfig:
    tenant_identifier: str
    initial_run_notify: bool = False
    stale_after_days: int = 3
    stale_alert_suppression_days: int = 3
    timezone: str = "Europe/Amsterdam"
    local_run_times: list[str] = field(default_factory=lambda: ["08:45", "16:45"])
    schedule_guard_enabled: bool = True
    schedule_guard_tolerance_minutes: int = 90
    run_log_max_entries: int = 500
    state_dir: str = "."
    github_run_url: str | None = None

    @classmethod
    def from_env(cls, tenant_api_url: str) -> "TrackerConfig":
        tenant_identifier = os.environ.get("TENANT_IDENTIFIER", "").strip()
        if not tenant_identifier:
            # Derive a non-sensitive label from the API host, never from credentials.
            from urllib.parse import urlparse

            tenant_identifier = urlparse(tenant_api_url).netloc or "qualys-tenant"

        run_url = None
        server = os.environ.get("GITHUB_SERVER_URL")
        repo = os.environ.get("GITHUB_REPOSITORY")
        run_id = os.environ.get("GITHUB_RUN_ID")
        if server and repo and run_id:
            run_url = f"{server}/{repo}/actions/runs/{run_id}"

        return cls(
            tenant_identifier=tenant_identifier,
            initial_run_notify=_env_bool("INITIAL_RUN_NOTIFY", False),
            stale_after_days=_env_int("STALE_AFTER_DAYS", 3),
            stale_alert_suppression_days=_env_int("STALE_ALERT_SUPPRESSION_DAYS", 3),
            timezone=os.environ.get("TRACKER_TIMEZONE", "Europe/Amsterdam").strip()
            or "Europe/Amsterdam",
            local_run_times=_env_list("TRACKER_LOCAL_RUN_TIMES", "08:45,16:45"),
            schedule_guard_enabled=_env_bool("TRACKER_SCHEDULE_GUARD_ENABLED", True),
            schedule_guard_tolerance_minutes=_env_int(
                "TRACKER_SCHEDULE_GUARD_TOLERANCE_MINUTES", 90
            ),
            run_log_max_entries=_env_int("RUN_LOG_MAX_ENTRIES", 500),
            state_dir=os.environ.get("TRACKER_STATE_DIR", ".").strip() or ".",
            github_run_url=run_url,
        )


@dataclass
class ReleaseNotesConfig:
    """Configuration for the public-release-notes correlation layer.

    See src/qualys_tracker/release_notes.py and release_intelligence.py.
    """

    index_url: str = "https://www.qualys.com/documentation/release-notes"
    check_public_releases: bool = True
    cache_ttl_days: int = 1
    public_release_notification: bool = True

    @classmethod
    def from_env(cls) -> "ReleaseNotesConfig":
        return cls(
            index_url=os.environ.get("QUALYS_RELEASE_NOTES_URL", "").strip()
            or "https://www.qualys.com/documentation/release-notes",
            check_public_releases=_env_bool("CHECK_PUBLIC_RELEASES", True),
            cache_ttl_days=_env_int("RELEASE_NOTES_CACHE_TTL_DAYS", 1),
            public_release_notification=_env_bool("PUBLIC_RELEASE_NOTIFICATION", True),
        )
