"""FX & Flows tab (placeholder)."""

from components import SourceRow, TabSpec, placeholder_card

SPEC = TabSpec(
    "FX & Flows",
    "USD/CLP and the flows behind it: BCCh operations, formal market, non-residents, AFPs.",
    [
        "USD/CLP",
        "BCCh FX operations",
        "Formal FX market flows",
        "Non-resident SPC positioning (BDE daily derivatives tables)",
        "AFP flows (Superintendencia de Pensiones, VERIFY)",
    ],
    [
        SourceRow(
            "USD/CLP, FX ops, derivatives",
            "BCCh BDE API",
            "REST GetSeries",
            "BCCH_API_TOKEN",
            "Daily",
        ),
        SourceRow(
            "FX market flows", "BCCh Informativo Diario", "PDF (pdfplumber)", "none", "Daily, T-1"
        ),
        SourceRow("AFP flows", "Superintendencia de Pensiones", "VERIFY", "none", "Monthly"),
    ],
)


def render() -> None:
    placeholder_card(SPEC, key="fx_flows")
