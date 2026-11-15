"""Chile Macro tab (placeholder)."""

from components import SourceRow, TabSpec, placeholder_card

SPEC = TabSpec(
    "Chile Macro",
    "Chilean inflation, activity, labor, trade and FX, plus the CPI nowcast and UF carry tools.",
    [
        "CPI headline, core (IPC SAE), goods/services (base 2023=100)",
        "IMACEC: mining vs non-mining, 3m/3m SAAR",
        "Labor market and trade",
        "USD/CLP dólar observado",
        "Tool: UF carry calculator",
        "Tool: CPI seasonality / nowcast table",
        "Tool: surprise index (actual minus EEE median)",
        "Data vintages (revisions)",
    ],
    [
        SourceRow(
            "CPI, IMACEC, labor, trade",
            "BCCh BDE API",
            "REST GetSeries",
            "BCCH_API_TOKEN",
            "Monthly",
        ),
        SourceRow(
            "UF, dólar observado", "BCCh BDE API", "REST GetSeries", "BCCH_API_TOKEN", "Daily"
        ),
        SourceRow("EEE medians", "BCCh BDE API", "REST GetSeries", "BCCH_API_TOKEN", "Monthly"),
        SourceRow("Release calendar", "INE Chile", "scraping", "none", "Annual / monthly"),
    ],
)


def render() -> None:
    placeholder_card(SPEC, key="chile_macro")
