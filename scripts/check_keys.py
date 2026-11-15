"""Run every source's health_check() and print a status table.

Usage: uv run python scripts/check_keys.py
"""

from __future__ import annotations

import sys

from monitor.http import redact
from monitor.sources import HealthResult, all_sources

ICONS = {"ok": "✅", "fail": "❌", "no_key": "❌", "stub": "⏸️"}


def main() -> int:
    rows: list[tuple[str, str, str, str]] = []
    for src in all_sources():
        try:
            res = src.health_check()
        except Exception as e:  # health checks must never crash the table
            res = HealthResult(False, f"{type(e).__name__}: {e}", "fail")
        status = res.status or ("ok" if res.ok else "fail")
        rows.append((ICONS.get(status, "❌"), src.name, src.env_key or "-", redact(res.message)))

    w_name = max(len(r[1]) for r in rows)
    w_key = max(len(r[2]) for r in rows)
    print(f"   {'source':<{w_name}}  {'key':<{w_key}}  result")
    print(f"   {'-' * w_name}  {'-' * w_key}  {'-' * 40}")
    for icon, name, key, msg in rows:
        print(f"{icon} {name:<{w_name}}  {key:<{w_key}}  {msg}")
    print("\n✅ ok   ❌ failed / key missing   ⏸️ stub (not implemented yet)")
    live = [r for r in rows if r[0] != ICONS["stub"]]
    return 0 if all(r[0] == ICONS["ok"] for r in live) else 1


if __name__ == "__main__":
    sys.exit(main())
