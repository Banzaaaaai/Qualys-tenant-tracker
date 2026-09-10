"""Generates tenant_report.json and tenant_report.md from a snapshot."""

from __future__ import annotations

import json


def write_json_report(path: str, snapshot: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2, sort_keys=True)
        fh.write("\n")


def write_markdown_report(path: str, snapshot: dict) -> None:
    tenant = snapshot.get("tenant", {})
    modules = snapshot.get("modules", {})
    lines = [
        "# Qualys Tenant Version Report",
        "",
        f"- **Tenant identifier:** {tenant.get('identifier', 'unknown')}",
        f"- **Last checked:** {snapshot.get('last_checked', 'unknown')}",
        f"- **Total modules:** {len(modules)}",
        "",
        "| Module | API Field | Current Version | First Seen | Last Changed |",
        "| ------ | --------- | ---------------- | ---------- | ------------- |",
    ]
    for module in sorted(modules):
        info = modules[module]
        lines.append(
            f"| {module} | {info.get('api_field', '')} | {info.get('version', '')} | "
            f"{info.get('first_seen', '')} | {info.get('last_changed', '')} |"
        )
    lines.append("")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
