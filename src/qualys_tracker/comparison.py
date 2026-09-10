"""Change detection between a stored snapshot and a fresh API response.

Comparison is deliberately a plain string inequality (`old != new`).
Qualys version strings do not follow one consistent scheme (see
README), so no numeric ordering is attempted -- only "did it change".
"""

from __future__ import annotations

from .models import ChangeType, ComparisonResult, ModuleChange, ModuleVersion


def compare(
    previous_modules: dict[str, dict],
    current_modules: list[ModuleVersion],
) -> ComparisonResult:
    """Compare previous snapshot modules against the current API response.

    `previous_modules` is the `modules` dict from a loaded snapshot,
    keyed by normalized module name, each value containing at least a
    `version` key (and optionally `api_field`, `first_seen`, etc).
    """
    result = ComparisonResult(total_current=len(current_modules))
    current_by_name = {m.module: m for m in current_modules}

    for module_name, current in current_by_name.items():
        if module_name not in previous_modules:
            result.new.append(
                ModuleChange(
                    module=module_name,
                    api_field=current.api_field,
                    old_version=None,
                    new_version=current.version,
                    change_type=ChangeType.NEW_MODULE,
                )
            )
            continue

        previous_version = previous_modules[module_name].get("version")
        if previous_version != current.version:
            result.changed.append(
                ModuleChange(
                    module=module_name,
                    api_field=current.api_field,
                    old_version=previous_version,
                    new_version=current.version,
                    change_type=ChangeType.VERSION_CHANGED,
                )
            )
        else:
            result.unchanged.append(module_name)

    for module_name, previous in previous_modules.items():
        if module_name not in current_by_name:
            result.removed.append(
                ModuleChange(
                    module=module_name,
                    api_field=previous.get("api_field", f"{module_name}-VERSION"),
                    old_version=previous.get("version"),
                    new_version=None,
                    change_type=ChangeType.MODULE_REMOVED,
                )
            )

    return result
