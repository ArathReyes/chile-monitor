"""Source registry."""

from monitor.sources.banxico import BanxicoSource
from monitor.sources.base import HealthResult, Source, StubSource
from monitor.sources.bcb import BcbSource
from monitor.sources.bcch import BcchSource
from monitor.sources.bcch_web import BcchWebSource
from monitor.sources.fred import FredSource
from monitor.sources.hacienda import HaciendaSource
from monitor.sources.ine import IneSource
from monitor.sources.kalshi import KalshiSource
from monitor.sources.polymarket import PolymarketSource
from monitor.sources.yfinance_src import YFinanceSource


def all_sources() -> list[Source]:
    return [
        BcchSource(),
        FredSource(),
        KalshiSource(),
        BanxicoSource(),
        PolymarketSource(),
        YFinanceSource(),
        BcbSource(),
        HaciendaSource(),
        BcchWebSource(),
        IneSource(),
    ]


__all__ = ["HealthResult", "Source", "StubSource", "all_sources"]
