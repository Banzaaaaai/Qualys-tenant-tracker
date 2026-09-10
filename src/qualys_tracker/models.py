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
