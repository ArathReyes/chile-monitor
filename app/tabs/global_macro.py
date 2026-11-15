"""Global Macro tab (placeholder)."""

from components import SourceRow, TabSpec, placeholder_card

SPEC = TabSpec(
    "Global Macro",
    "US rates and data, Fed pricing, and how much of Chile's move is global vs idiosyncratic.",
    [
        "US rates and macro data",
        "Fed decision probabilities",
        "Transmission panel: rolling betas of Chile / Mexico / Colombia rates to UST 2y/10y, DXY, "
        "copper; residual 'idiosyncratic' move (after Dirk Willer, Trading Global Macro Markets)",
    ],
    [
        SourceRow("UST, SOFR, US CPI/PCE/NFP", "FRED", "REST", "FRED_API_KEY", "Daily / monthly"),
        SourceRow("Fed probabilities", "Kalshi Trade API v2", "REST (public)", "none", "Live"),
        SourceRow("DXY, copper", "yfinance", "Python lib (unofficial)", "none", "Daily"),
        SourceRow("Chile rates", "BCCh BDE API", "REST", "BCCH_API_TOKEN", "Daily, T-1"),
        SourceRow("Mexico rates", "Banxico SIE", "REST", "BANXICO_TOKEN", "Daily"),
        SourceRow("Colombia rates", "BanRep", "VERIFY", "VERIFY", "Daily"),
    ],
)


def render() -> None:
    placeholder_card(SPEC, key="global_macro")
