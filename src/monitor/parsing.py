"""Shared parsers for Chilean number and date formats.

Chilean sources write numbers with a decimal comma and thousands dot (``1.234,56``) and dates
like ``02.Ene.2026``. The BDE REST API is the exception: it returns dot-decimal strings and
``DD-MM-YYYY`` dates, handled by ``parse_bde_value`` / ``parse_bde_date``.

Parsers raise ``ValueError`` on anything they do not recognise (fail loudly). Explicit
missing-value markers return ``None``; callers drop those rows, never fill them.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime

MISSING_MARKERS = frozenset({"", "-", "--", "—", "n.d.", "nd", "s/i", "nan", "na", "n/a"})

MONTHS_ES: dict[str, int] = {
    "ene": 1, "enero": 1,
    "feb": 2, "febrero": 2,
    "mar": 3, "marzo": 3,
    "abr": 4, "abril": 4,
    "may": 5, "mayo": 5,
    "jun": 6, "junio": 6,
    "jul": 7, "julio": 7,
    "ago": 8, "agosto": 8,
    "sep": 9, "sept": 9, "set": 9, "septiembre": 9, "setiembre": 9,
    "oct": 10, "octubre": 10,
    "nov": 11, "noviembre": 11,
    "dic": 12, "diciembre": 12,
}  # fmt: skip

_CL_NUMBER = re.compile(r"^[+-]?(\d{1,3}(\.\d{3})+|\d+)(,\d+)?$")


def _clean(text: str) -> str:
    return text.replace(" ", " ").strip()


def parse_cl_number(text: str | None) -> float | None:
    """Parse a Chilean-format number: ``4,50`` -> 4.5, ``1.234,5`` -> 1234.5, ``-0,25%`` -> -0.25.

    Returns None for missing markers. Raises ValueError for anything else unrecognised,
    including ambiguous dot-decimal input like ``4.50``.
    """
    if text is None:
        return None
    s = _clean(text)
    if s.lower() in MISSING_MARKERS:
        return None
    s = s.removesuffix("%").strip().replace(" ", "")
    if s.startswith("(") and s.endswith(")"):  # accounting negative
        s = "-" + s[1:-1]
    if not _CL_NUMBER.match(s):
        raise ValueError(f"Not a Chilean-format number: {text!r}")
    return float(s.replace(".", "").replace(",", "."))


def parse_cl_date(text: str) -> date:
    """Parse common Chilean date formats.

    Supported: ``02.Ene.2026``, ``02-Ene-2026``, ``2 de enero de 2026``, ``02-01-2026``,
    ``02/01/2026`` (day first) and ISO ``2026-01-02``.
    """
    s = _clean(text).lower()
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s):
        return date(int(m[1]), int(m[2]), int(m[3]))
    if m := re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", s):
        return date(int(m[3]), int(m[2]), int(m[1]))
    if m := re.fullmatch(r"(\d{1,2})[.\-/ ]([a-záéíóú]+)\.?[.\-/ ](\d{4})", s):
        return date(int(m[3]), _month(m[2], text), int(m[1]))
    if m := re.fullmatch(r"(\d{1,2}) de ([a-záéíóú]+) de (\d{4})", s):
        return date(int(m[3]), _month(m[2], text), int(m[1]))
    raise ValueError(f"Unrecognised Chilean date: {text!r}")


def _month(token: str, original: str) -> int:
    try:
        return MONTHS_ES[token.strip(".")]
    except KeyError:
        raise ValueError(f"Unknown Spanish month in {original!r}") from None


def parse_bde_value(text: str | None) -> float | None:
    """Parse a BDE REST ``value`` string (dot decimal). ``"NaN"`` -> None."""
    if text is None:
        return None
    s = text.strip()
    if s.lower() in MISSING_MARKERS:
        return None
    value = float(s)
    return None if math.isnan(value) else value


def parse_bde_date(text: str) -> date:
    """Parse a BDE REST ``indexDateString`` (``DD-MM-YYYY``)."""
    return datetime.strptime(text.strip(), "%d-%m-%Y").date()
