"""Copper tab (placeholder)."""

from components import SourceRow, TabSpec, placeholder_card

SPEC = TabSpec(
    "Copper",
    "Copper as Chile's terms-of-trade driver, and its link to CLP.",
    [
        "COMEX copper price",
        "Copper-CLP rolling beta",
        "China indicators",
        "Cochilco production (VERIFY access)",
    ],
    [
        SourceRow("COMEX copper (HG=F)", "yfinance", "Python lib (unofficial)", "none", "Daily"),
        SourceRow("USD/CLP", "BCCh BDE API", "REST", "BCCH_API_TOKEN", "Daily"),
        SourceRow("Production", "Cochilco", "VERIFY", "none", "Monthly"),
    ],
)


def render() -> None:
    placeholder_card(SPEC, key="copper")
