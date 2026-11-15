"""Rebuild the BCCh past-meeting list in config/meetings.yaml from ingested statements.

Past meetings come from parsed press releases (confirmed). Existing entries for dates with no
statement (e.g. future meetings sourced from market rules) are kept unchanged.

Usage: uv run python scripts/sync_meetings_from_statements.py
"""

from __future__ import annotations

import yaml

from monitor import storage
from monitor.config import CONFIG_DIR
from monitor.models.statements import parse_statement

PATH = CONFIG_DIR / "meetings.yaml"


def main() -> None:
    conn = storage.connect()
    try:
        docs = storage.read_documents(conn, "statements")
    finally:
        conn.close()
    from_statements = {}
    for r in docs.itertuples(index=False):
        p = parse_statement(r.text)
        from_statements[p.meeting_date] = {
            "meeting_id": p.meeting_id,
            "decision_date": p.meeting_date,
            "source": "BCCh press release (statement)",
            "confirmed": True,
        }
    header = PATH.read_text(encoding="utf-8").split("bcch:")[0]
    cfg = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    kept = [m for m in cfg["bcch"]["meetings"] if m["decision_date"] not in from_statements]
    merged = sorted([*from_statements.values(), *kept], key=lambda m: m["decision_date"])
    cfg["bcch"]["meetings"] = merged
    body = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=110)
    PATH.write_text(header + body, encoding="utf-8")
    print(f"{len(from_statements)} meetings from statements, {len(kept)} kept from YAML")


if __name__ == "__main__":
    main()
