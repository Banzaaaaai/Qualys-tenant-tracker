"""Shared data structures used across the tracker."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ChangeType(str, Enum):
    NEW_MODULE = "NEW_MODULE"
    VERSION_CHANGED = "VERSION_CHANGED"
    MODULE_REMOVED = "MODULE_REMOVED"


@dataclass(frozen=True)
class ModuleVersion:
    """One `<X>-VERSION` field as reported by the API right now."""

    api_field: str
    module: str
    version: str


@dataclass
class ModuleChange:
    module: str
    api_field: str
    old_version: str | None
    new_version: str | None
    change_type: ChangeType

    def to_history_entry(self, timestamp: str) -> dict:
        return {
            "timestamp": timestamp,
            "module": self.module,
            "api_field": self.api_field,
            "old_version": self.old_version,
            "new_version": self.new_version,
            "change_type": self.change_type.value,
        }


@dataclass
class ComparisonResult:
    new: list[ModuleChange] = field(default_factory=list)
    changed: list[ModuleChange] = field(default_factory=list)
    removed: list[ModuleChange] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    total_current: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.new or self.changed or self.removed)

    @property
    def all_changes(self) -> list[ModuleChange]:
        return [*self.changed, *self.new, *self.removed]

    @property
    def changed_count(self) -> int:
        return len(self.changed) + len(self.new) + len(self.removed)


@dataclass
class Feature:
    """One capability/enhancement extracted from a release note."""

    title: str
    description: str = ""

    def to_dict(self) -> dict:
        return {"title": self.title, "description": self.description}

    @classmethod
    def from_dict(cls, data: dict) -> "Feature":
        return cls(title=data.get("title", ""), description=data.get("description", ""))


@dataclass
class ReleaseInfo:
    """A single official Qualys release note, for one module/version."""

    version: str
    url: str
    release_date: str | None = None
    title: str | None = None
    features: list[Feature] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "url": self.url,
            "release_date": self.release_date,
            "title": self.title,
            "features": [f.to_dict() for f in self.features],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ReleaseInfo":
        return cls(
            version=data["version"],
            url=data["url"],
            release_date=data.get("release_date"),
            title=data.get("title"),
            features=[Feature.from_dict(f) for f in data.get("features", [])],
        )


class UpgradeStatus(str, Enum):
    CURRENT = "CURRENT"
    PUBLIC_NEWER_VERSION_AVAILABLE = "PUBLIC_NEWER_VERSION_AVAILABLE"
    PUBLIC_RELEASE_NOT_FOUND = "PUBLIC_RELEASE_NOT_FOUND"
    VERSION_ORDER_UNKNOWN = "VERSION_ORDER_UNKNOWN"
    RELEASE_NOTE_LOOKUP_FAILED = "RELEASE_NOTE_LOOKUP_FAILED"


@dataclass
class ModuleReleaseIntelligence:
    """Correlates one module's tenant version against public release notes.

    `tenant_release` is None when the tenant's exact version could not be
    matched in the official release notes (Strategy 4 -- "not found" is
    reported, never fabricated). `latest_public_release` is None when no
    public release could be found at all for the module, or when ordering
    among candidates could not be safely determined (see version_compare.py).
    """

    module: str
    tenant_version: str
    tenant_release: ReleaseInfo | None
    latest_public_release: ReleaseInfo | None
    upgrade_status: UpgradeStatus
    error: str | None = None
