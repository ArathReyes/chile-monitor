"""Parse the EEE results PDF ("Gráficos (distribución de respuestas)") summary table.

The first page lists, per variable and horizon: median, decile 1, decile 9 and number of
responses, e.g. ``En la siguiente reunión 4,50 4,50 4,50 49 Graf15``. Layout confirmed on the
October 2026 PDF (fixture: tests/fixtures/eee_pdf_page1_oct2026.txt). The PDF also carries the
"next meeting" / "following meeting" TPM horizons that the BDE does not publish.
"""

from __future__ import annotations

import io
import re
from datetime import date

import pandas as pd

from monitor.parsing import MONTHS_ES, parse_cl_number

_HEADER = re.compile(r"ENCUESTA DE EXPECTATIVAS ECON[ÓO]MICAS\s+([A-Za-záéíóú]+)\s+(\d{4})", re.I)
_SECTION = re.compile(r"^(\d{2})\.\s*(.+)$")
_ROW = re.compile(
    r"^(?P<label>.+?)\s+(?P<med>-?[\d.]+,\d+)\s+(?P<d1>-?[\d.]+,\d+)\s+(?P<d9>-?[\d.]+,\d+)"
    r"\s+(?P<n>\d+)\s+Graf\d+\s*$"
)
COLUMNS = [
    "survey",
    "survey_month",
    "section_no",
    "section",
    "horizon",
    "median",
    "p10",
    "p90",
    "n",
]


def survey_month(text: str) -> date:
    m = _HEADER.search(text)
    if not m:
        raise ValueError("EEE PDF: header with survey month not found (layout changed?)")
    month = MONTHS_ES.get(m[1].lower())
    if month is None:
        raise ValueError(f"EEE PDF: unknown month {m[1]!r}")
    return date(int(m[2]), month, 1)


def parse_summary(text: str) -> pd.DataFrame:
    """All variable/horizon rows on the summary page. Raises if nothing parses."""
    month = survey_month(text)
    rows, sec_no, sec = [], None, None
    for line in text.splitlines():
        line = line.strip()
        if m := _SECTION.match(line):
            sec_no, sec = m[1], m[2].strip()
            continue
        if sec and (m := _ROW.match(line)):
            rows.append(
                ("EEE", month, sec_no, sec, m["label"].strip(), parse_cl_number(m["med"]),
                 parse_cl_number(m["d1"]), parse_cl_number(m["d9"]), int(m["n"]))
            )  # fmt: skip
    if not rows:
        raise ValueError("EEE PDF: no summary rows parsed (layout changed?)")
    return pd.DataFrame(rows, columns=COLUMNS)


def tpm_rows(df: pd.DataFrame) -> pd.DataFrame:
    out = df[df["section"].str.contains("Pol[ií]tica", regex=True)]
    if out.empty:
        raise ValueError("EEE PDF: TPM section not found")
    return out.reset_index(drop=True)


def parse_pdf(content: bytes) -> pd.DataFrame:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        text = pdf.pages[0].extract_text() or ""
    return parse_summary(text)
