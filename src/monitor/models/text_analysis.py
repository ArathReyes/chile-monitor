"""Text tools for BCCh statements and minutes. Pure functions.

Vote extraction is intentionally not implemented yet: it must be built and tested against real
statement text (pending the manually saved documents, see docs/manual-data.md).
"""

from __future__ import annotations

import difflib
import html
import re

_TOKEN = re.compile(r"\s+|[^\s]+")
INS_STYLE = "background:#d4f7d4;color:#0a5c0a"
DEL_STYLE = "background:#fbd5d5;color:#8a1010;text-decoration:line-through"


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def word_diff(old: str, new: str) -> list[tuple[str, str]]:
    """Word-level diff as (op, text) with op in {'equal', 'insert', 'delete'}."""
    a, b = _tokens(old), _tokens(new)
    out: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            out.append(("equal", "".join(a[i1:i2])))
        else:
            if i2 > i1:
                out.append(("delete", "".join(a[i1:i2])))
            if j2 > j1:
                out.append(("insert", "".join(b[j1:j2])))
    return out


def diff_html(old: str, new: str) -> str:
    """HTML with insertions in green and deletions in red (struck through)."""
    parts = []
    for op, text in word_diff(old, new):
        t = html.escape(text).replace("\n", "<br>")
        if op == "insert":
            parts.append(f'<span style="{INS_STYLE}">{t}</span>')
        elif op == "delete":
            parts.append(f'<span style="{DEL_STYLE}">{t}</span>')
        else:
            parts.append(t)
    return "".join(parts)


def diff_stats(old: str, new: str) -> dict[str, int | float]:
    ops = word_diff(old, new)
    ins = sum(len(t.split()) for op, t in ops if op == "insert")
    dele = sum(len(t.split()) for op, t in ops if op == "delete")
    total = max(len(new.split()), 1)
    return {"words_added": ins, "words_removed": dele, "share_changed": (ins + dele) / total}
