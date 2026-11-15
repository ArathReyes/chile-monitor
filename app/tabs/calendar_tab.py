"""Calendar tab (placeholder). Named calendar_tab to avoid shadowing stdlib `calendar`."""

from components import SourceRow, TabSpec, placeholder_card

SPEC = TabSpec(
    "Calendar",
    "Everything scheduled that can move Chilean rates, with consensus and last surprise.",
    [
        "BCCh meetings and IPoM",
        "INE releases (CPI, labor)",
        "Hacienda auctions",
        "US releases and FOMC",
        "Consensus and last surprise per event",
    ],
    [
        SourceRow("BCCh / FOMC meetings", "config/meetings.yaml", "local YAML", "none", "Annual"),
        SourceRow("INE releases", "INE Chile", "scraping", "none", "Annual / monthly"),
        SourceRow("Treasury auctions", "Hacienda ODP", "scraping", "none", "Per calendar update"),
        SourceRow("US release dates", "FRED", "REST (releases)", "FRED_API_KEY", "Ongoing"),
    ],
)


def render() -> None:
    placeholder_card(SPEC, key="calendar")
