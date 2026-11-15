"""Curves & Rates tab (placeholder)."""

from components import SourceRow, TabSpec, placeholder_card

SPEC = TabSpec(
    "Curves & Rates",
    "Chilean nominal, real and swap curves with changes, carry and spreads to UST.",
    [
        "Nominal BCP/BTP and real BCU/BTU curves; breakevens",
        "SPC curve, nominal and UF",
        "1d / 1w / 1m changes",
        "Carry and rolldown",
        "Spread vs UST",
    ],
    [
        SourceRow(
            "Bond secondary rates, SPC",
            "BCCh BDE API",
            "REST GetSeries",
            "BCCH_API_TOKEN",
            "Daily, T-1, transaction-based (gaps)",
        ),
        SourceRow(
            "T-1 SPC and bond rates",
            "BCCh Informativo Diario",
            "PDF (pdfplumber)",
            "none",
            "Daily, T-1",
        ),
        SourceRow("UST yields", "FRED", "REST", "FRED_API_KEY", "Daily"),
    ],
)


def render() -> None:
    placeholder_card(SPEC, key="curves_rates")
