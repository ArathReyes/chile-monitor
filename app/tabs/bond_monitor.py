"""Bond Monitor tab (placeholder, pinned for later)."""

from components import SourceRow, TabSpec, placeholder_card

SPEC = TabSpec(
    "Bond Monitor",
    "Chilean Treasury supply: auction calendar, results and DV01 going to market each week.",
    [
        "Hacienda auction calendar, auto-scraped (bond, date, size)",
        "Auction results: bid-to-cover, allocation to banks / AFPs / others",
        "DV01 per auction from the bond master (coupon, maturity from the nemotécnico) "
        "and BDE yields",
        "Weekly DV01 supply",
    ],
    [
        SourceRow(
            "Auction calendar & results",
            "Hacienda ODP",
            "scraping",
            "none",
            "Per calendar update / per auction",
        ),
        SourceRow(
            "Auction results", "BCCh Informativo Diario", "PDF (pdfplumber)", "none", "Daily, T-1"
        ),
        SourceRow("Secondary yields", "BCCh BDE API", "REST", "BCCH_API_TOKEN", "Daily, T-1"),
    ],
)


def render() -> None:
    placeholder_card(SPEC, key="bond_monitor")
