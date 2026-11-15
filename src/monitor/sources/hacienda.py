"""Hacienda (Chile), Oficina de la Deuda Pública: Treasury auction calendar, results, bond master.

Adapted from the user-provided standalone ``scripts/hacienda.py`` (parsers, accumulation and the
refresh-on-consult rule kept; HTTP, storage and logging moved to the repo's shared modules).
Plain Python, no Claude session needed: runs from the CLI, cron/launchd and the dashboard.

Sources (all verified 2026-11-15; www.hacienda.cl has no robots.txt and no bot protection)
------------------------------------------------------------------------------------------
* Calendario de Licitaciones (HTML, current quarter): stacked header rows group → unit →
  instrument → ISIN → "Ticker" (nemo) → "Venc." (maturity); one row per date ("05-nov-26").
* Resultados Última Licitación (HTML): only the latest auction. Also on the SOMA page.
* SOMA page text: "Del endeudamiento autorizado total para el año 2026 de USD 23.600 millones …
  el monto acumulado a la fecha asciende a USD 21.xxx millones" (thousands dot).
* Estadísticas → "Resultados Licitaciones YYYY (consolidado)": one .xlsx per year (2021-2026),
  sheet "Licitaciones Bonos"; the header row position and columns differ by year (2021-22 add
  "Fecha Emisión" and write units in words); void auctions say "desierta"; some maturities are
  stored with the wrong century (2031 → 1931), corrected here.
* Estadísticas → "Bonos de Tesorería - Stock Total": .xlsx, sheet "Stock": currency, ISIN, nemo,
  issue/maturity, coupon (fraction or %), outstanding, unit, ESG. Local (Pesos/UF) rows only.
* Estadísticas → "Características Financieras - Bonos Locales": HTML, one table per issue year;
  coupon payment months ("mar/sep", "15-Abr / 15-Oct"): coupons are semiannual.

Usage
-----
    uv run python -m monitor.sources.hacienda calendar | results | all | soma | master
    uv run python -m monitor.sources.hacienda watch     # auction days: poll results until stored
    uv run python -m monitor.sources.hacienda backfill  # consolidated files 2021-current year
    uv run python -m monitor.sources.hacienda show | debug
"""

from __future__ import annotations

import argparse
import io
import logging
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
from bs4 import BeautifulSoup

from monitor import storage
from monitor.config import SeriesSpec
from monitor.http import HttpClient, HttpError
from monitor.sources.base import HealthResult, Source

BASE = (
    "https://www.hacienda.cl/areas-de-trabajo/finanzas-internacionales/oficina-de-la-deuda-publica"
)
URL_CALENDAR = f"{BASE}/plan-emision/calendario-de-licitaciones"
URL_RESULTS = f"{BASE}/colocaciones-bcch-soma/resultados-ultima-licitacion"
URL_RESULTS_ALT = f"{BASE}/colocaciones-bcch-soma"  # also shows the latest result + SOMA text
URL_SOMA = URL_RESULTS_ALT
URL_STATS = f"{BASE}/estadisticas"
URL_CHARACTERISTICS = f"{URL_STATS}/caracteristicas-financieras-bonos-locales"
URL_STOCK = f"{URL_STATS}/bonos-de-tesoreria-stock-total-bonos-locales-y-externos"
FIRST_CONSOLIDATED_YEAR = 2021

SANTIAGO = ZoneInfo("America/Santiago")
log = logging.getLogger(__name__)

SPANISH_MONTHS = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "set": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}

# --------------------------------------------------------------------------- parsing helpers


def norm(s: str | None) -> str:
    """Lowercase, strip accents and collapse whitespace."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def parse_number(s: Any) -> float | None:
    """Chilean format: '50.000' -> 50000, '2,81%' -> 2.81, '-' / '' -> None."""
    if s is None:
        return None
    if isinstance(s, int | float):
        return None if pd.isna(s) else float(s)
    t = str(s).strip().replace("%", "").replace("$", "").replace("\xa0", "").replace(" ", "")
    if t in {"", "-", "–", "—", "n/a", "N/A"}:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def parse_date(s: Any) -> date | None:
    """Accepts '05-nov-26', '5-nov-2026', '01-sept-30', '12/11/2026', '12-11-2026', '2026-11-12'."""
    if not s:
        return None
    t = norm(s).replace(".", "")
    m = re.search(r"(\d{1,2})[-/ ]([a-z]{3})[a-z]*[-/ ](\d{2,4})", t)
    if m:
        d, mon, y = int(m.group(1)), SPANISH_MONTHS.get(m.group(2)), int(m.group(3))
        if mon:
            return date(2000 + y if y < 100 else y, mon, d)
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})", t)
    if m:
        y = int(m.group(3))
        return date(2000 + y if y < 100 else y, int(m.group(2)), int(m.group(1)))
    return None


ISIN_RE = re.compile(r"\bCL\w{10}\b")
NEMO_RE = re.compile(r"\b(BTP|BTU|BCP|BCU|PDBC|LTP|LTU)[0-9A-Z]{4,12}\b")
INSTR_RE = re.compile(r"(letra\s+[a-z]{3}\.?\s*\d{4}|bt[pu]-\d{4}(\s*\(r\))?)", re.I)
NEMO_CODE_RE = re.compile(r"^(BTP|BTU)(\d{3})(\d{2})(\d{2})$")


def table_grid(table) -> list[list[str]]:
    """Expand an HTML <table> into a rectangular text grid, honoring rowspan/colspan."""
    grid: list[list[str | None]] = []
    pending: dict[tuple[int, int], str] = {}
    for r, tr in enumerate(table.find_all("tr")):
        row: list[str | None] = []
        c = 0
        cells = tr.find_all(["td", "th"])
        ci = 0
        while ci < len(cells) or (r, c) in pending:
            if (r, c) in pending:
                row.append(pending.pop((r, c)))
                c += 1
                continue
            cell = cells[ci]
            ci += 1
            text = cell.get_text(" ", strip=True)
            rs = int(cell.get("rowspan", 1) or 1)
            cs = int(cell.get("colspan", 1) or 1)
            for k in range(cs):
                row.append(text)
                for dr in range(1, rs):
                    pending[(r + dr, c + k)] = text
            c += cs
        grid.append(row)
    width = max((len(r) for r in grid), default=0)
    return [[(x or "") for x in r] + [""] * (width - len(r)) for r in grid]


# --------------------------------------------------------------------------- nemo rule


@dataclass(frozen=True)
class NemoInfo:
    instrument_type: str  # Letra | BTP | BTU
    currency: str  # CLP | UF
    coupon_pct: float
    maturity_month: date  # first day of the maturity month


def decode_nemo(nemo: str | None) -> NemoInfo | None:
    """BTP/BTU + 3-digit coupon (one implied decimal) + MMYY: BTP0530732 → 5.3%, Jul-2032.

    Validated against every local bond in Hacienda's Stock Total and Características files
    (tests/test_hacienda.py). A zero-coupon BTP is a Letra (BTP000MMYY); BTU0001028 is a
    zero-coupon UF bond, still a BTU. Used only as a fallback when the master lacks a bond.
    """
    m = NEMO_CODE_RE.match((nemo or "").strip().upper())
    if not m:
        return None
    kind, cpn, mm, yy = m.group(1), int(m.group(2)) / 10, int(m.group(3)), int(m.group(4))
    if not 1 <= mm <= 12:
        return None
    itype = "Letra" if kind == "BTP" and cpn == 0 else kind
    return NemoInfo(itype, "CLP" if kind == "BTP" else "UF", cpn, date(2000 + yy, mm, 1))


def instrument_type(instrument: str | None, nemo: str | None) -> str | None:
    if instrument and norm(instrument).startswith("letra"):
        return "Letra"
    info = decode_nemo(nemo)
    if info:
        return info.instrument_type
    if instrument:
        up = instrument.upper()
        return "BTU" if up.startswith("BTU") else "BTP" if up.startswith("BTP") else None
    return None


def bond_label(nemo: str) -> str:
    """Display name in Hacienda's style from a nemo: BTP-2032, BTU-2055, Letra oct. 2027."""
    info = decode_nemo(nemo)
    if info is None:
        return nemo
    if info.instrument_type == "Letra":
        mon = [k for k, v in SPANISH_MONTHS.items() if v == info.maturity_month.month][0]
        return f"Letra {mon}. {info.maturity_month.year}"
    return f"{nemo[:3]}-{info.maturity_month.year}"


# --------------------------------------------------------------------------- data classes


@dataclass(frozen=True)
class CalendarRow:
    auction_date: date
    instrument: str  # e.g. "BTP-2032", "Letra nov. 2027", "BTU-2055 (R)"
    nemo: str | None
    isin: str | None
    unit: str  # "MM CLP" or "M UF"
    amount: float
    reopening: bool
    maturity: date | None = None

    @property
    def instrument_type(self) -> str | None:
        return instrument_type(self.instrument, self.nemo)


@dataclass(frozen=True)
class AuctionResult:
    auction_date: date
    bond: str
    nemo: str
    maturity: date | None
    unit: str | None
    cupo: float | None  # amount offered
    demand: float | None  # amount bid
    allocated: float | None
    banks: float | None
    afp_ins_brokers: float | None
    rate: float | None  # %
    bid_to_cover: float | None
    status: str = "ok"  # ok | desierta (void auction)
    instrument_type: str | None = field(default=None)
    note: str | None = None  # data fixes applied by the loader (consolidated files)

    def record(self) -> dict:
        d = asdict(self)
        d["instrument_type"] = self.instrument_type or instrument_type(self.bond, self.nemo)
        return d


# --------------------------------------------------------------------------- page parsers


def _unit_for(instrument: str, header_text: str) -> str:
    h = norm(header_text)
    if "uf" in h and "peso" not in h:
        return "M UF"
    if "peso" in h:
        return "MM CLP"
    return "M UF" if instrument.upper().startswith("BTU") else "MM CLP"


def parse_calendar(html: str) -> list[CalendarRow]:
    """Find the table with 'Fecha de operación' and return one row per (date, instrument)."""
    soup = BeautifulSoup(html, "lxml")
    target = next(
        (t for t in soup.find_all("table") if "fecha de operacion" in norm(t.get_text(" "))), None
    )
    if target is None:
        raise ValueError(
            "Calendar table not found (no 'Fecha de operación' header). Page layout changed?"
        )
    grid = table_grid(target)
    first_data = next((i for i, r in enumerate(grid) if parse_date(r[0])), None)
    if first_data is None:
        raise ValueError("Calendar table has no date rows.")
    header_rows = grid[:first_data]
    venc_row = next((r for r in header_rows if norm(r[0]).startswith("venc")), None)

    cols: dict[int, dict] = {}
    for j in range(1, len(grid[0])):
        stack = [hr[j] for hr in header_rows if j < len(hr)]
        joined = " | ".join(stack)
        instr = next((m.group(0) for s in stack if (m := INSTR_RE.search(s))), None)
        if not instr:
            continue
        cols[j] = {
            "instrument": re.sub(r"\s+", " ", instr).strip(),
            "isin": next((m.group(0) for s in stack if (m := ISIN_RE.search(s))), None),
            "nemo": next((m.group(0) for s in stack if (m := NEMO_RE.search(s))), None),
            "unit": _unit_for(instr, joined),
            "reopening": "(r)" in norm(joined),
            "maturity": parse_date(venc_row[j]) if venc_row and j < len(venc_row) else None,
        }
    if not cols:
        raise ValueError("Could not identify instrument columns in calendar header.")

    out: list[CalendarRow] = []
    for r in grid[first_data:]:
        d = parse_date(r[0])
        if not d:
            continue
        for j, meta in cols.items():
            amt = parse_number(r[j]) if j < len(r) else None
            if amt is None or amt == 0:
                continue
            out.append(
                CalendarRow(
                    d,
                    meta["instrument"],
                    meta["nemo"],
                    meta["isin"],
                    meta["unit"],
                    amt,
                    meta["reopening"],
                    meta["maturity"],
                )
            )
    return out


RESULT_FIELDS = {
    # normalized header fragment -> field
    "fecha de licitacion": "auction_date",
    "bono": "bond",
    "nemo": "nemo",
    "fecha de vencimiento": "maturity",
    "unidad": "unit",
    "cupo": "cupo",
    "monto demandado": "demand",
    "total adjudicado": "allocated",
    "bancos": "banks",
    "afp": "afp_ins_brokers",
    "tasa": "rate",
}


def parse_results(html: str) -> list[AuctionResult]:
    """Parse the 'Resultados Última Licitación' table (one row per bond auctioned)."""
    soup = BeautifulSoup(html, "lxml")
    target = None
    for t in soup.find_all("table"):
        txt = norm(t.get_text(" "))
        if "nemo" in txt and "adjudicado" in txt:
            target = t
            break
    if target is None:
        raise ValueError(
            "Results table not found (needs 'Nemo' and 'Adjudicado'). Page layout changed?"
        )
    grid = table_grid(target)
    h_idx = next(i for i, r in enumerate(grid) if any("nemo" in norm(c) for c in r))
    header = [norm(c) for c in grid[h_idx]]
    colmap: dict[str, int] = {}
    for j, h in enumerate(header):
        for frag, fld in RESULT_FIELDS.items():
            if frag in h and fld not in colmap:
                colmap[fld] = j
                break
    missing = {"nemo", "allocated"} - colmap.keys()
    if missing:
        raise ValueError(f"Results table missing columns: {missing}. Header was: {header}")

    page_date = None
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", soup.get_text(" "))
    if m:
        page_date = parse_date(m.group(1))

    out: list[AuctionResult] = []
    for r in grid[h_idx + 1 :]:

        def get(f: str, r: list[str] = r) -> str | None:
            return r[colmap[f]] if f in colmap and colmap[f] < len(r) else None

        nemo = (get("nemo") or "").strip()
        if not nemo or norm(nemo) == "nemo":
            continue
        void = "desierta" in norm(" ".join(r))
        cupo, demand, alloc = (parse_number(get(f)) for f in ("cupo", "demand", "allocated"))
        ad = parse_date(get("auction_date")) or page_date
        if ad is None:
            raise ValueError("Could not determine auction date for results row.")
        bond = (get("bond") or "").strip()
        out.append(
            AuctionResult(
                auction_date=ad,
                bond=bond,
                nemo=nemo,
                maturity=parse_date(get("maturity")),
                unit=_result_unit(get("unit"), nemo),
                cupo=cupo,
                demand=None if void else demand,
                allocated=None if void else alloc,
                banks=None if void else parse_number(get("banks")),
                afp_ins_brokers=None if void else parse_number(get("afp_ins_brokers")),
                rate=None if void else parse_number(get("rate")),
                bid_to_cover=round(demand / cupo, 3) if (demand and cupo and not void) else None,
                status="desierta" if void else "ok",
                instrument_type=instrument_type(bond, nemo),
            )
        )
    if not out:
        raise ValueError("Results table parsed but contained no rows.")
    return out


def _result_unit(raw: str | None, nemo: str) -> str:
    n = norm(raw)
    if "uf" in n:
        return "M UF"
    if "peso" in n or n == "clp":
        return "MM CLP"
    return "M UF" if nemo.upper().startswith("BTU") else "MM CLP"


@dataclass(frozen=True)
class SomaStatus:
    year: int
    authorized_usd_mm: float
    placed_usd_mm: float


SOMA_AUTH_RE = re.compile(r"autorizado total para el ano (\d{4}) de usd ([\d.,]+) millones", re.I)
SOMA_PLACED_RE = re.compile(r"monto acumulado a la fecha asciende a usd ([\d.,]+) millones", re.I)


def parse_soma(html: str) -> SomaStatus:
    """Annual authorized borrowing and cumulative placements (USD millions) from the SOMA page."""
    text = norm(BeautifulSoup(html, "lxml").get_text(" "))
    a, p = SOMA_AUTH_RE.search(text), SOMA_PLACED_RE.search(text)
    if not a or not p:
        raise ValueError("SOMA authorization sentence not found. Page wording changed?")
    auth, placed = parse_number(a.group(2)), parse_number(p.group(1))
    if auth is None or placed is None or not 0 < placed <= auth * 1.5:
        raise ValueError(f"SOMA amounts look wrong: authorized {auth}, placed {placed}")
    return SomaStatus(int(a.group(1)), auth, placed)


def consolidated_links(html: str) -> dict[int, str]:
    """{year: absolute URL} for 'Resultados Licitaciones YYYY (consolidado)' links."""
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r"resultados licitaciones (\d{4}) \(consolidado\)", norm(a.get_text(" ")))
        if m:
            out[int(m.group(1))] = urljoin(URL_STATS, a["href"])
    if not out:
        raise ValueError("No 'Resultados Licitaciones YYYY (consolidado)' links found.")
    return out


# --------------------------------------------------------------------------- Excel loaders

CONS_COLS = {
    "codigo nemotecnico": "nemo",
    "isin": "isin",
    "moneda": "unit",
    "fecha emision": "issue_date",
    "fecha madurez": "maturity",
    "fecha de licitacion": "auction_date",
    "cupo": "cupo",
    "monto demandado": "demand",
    "adjudicado": "allocated",
    "tasa de interes": "rate",
}


def _excel_date(v: Any) -> date | None:
    """Excel cell -> date. Hacienda's sheets store some 20xx dates as 19xx (two-digit-year
    entry: Excel reads 30-99 as 1930-1999); years before 2000 get +100 (no bond or auction in
    these files predates 2000)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime | pd.Timestamp):
        d = v.date()
    elif isinstance(v, date):
        d = v
    elif isinstance(v, int | float):
        d = (datetime(1899, 12, 30) + timedelta(days=float(v))).date()
    else:
        d = parse_date(v)
    if d is not None and d.year < 2000:
        d = d.replace(year=d.year + 100)
    return d


def _check_dates(
    nemo: str, ad: date, maturity: date | None, file_year: int | None
) -> tuple[date, date | None, str | None]:
    """Fix typos Hacienda's consolidated files are known to contain, noting each fix:
    an auction dated outside the file's year (2033 in the 2023 file) is moved to the file's
    year; a maturity that contradicts the nemo's MMYY (2020 for BTP0470930) is blanked (the
    bond master has the real one)."""
    notes = []
    if file_year and ad.year != file_year:
        fixed = ad.replace(year=file_year)
        notes.append(f"auction date {ad} outside file year; read as {fixed}")
        ad = fixed
    info = decode_nemo(nemo)
    if (
        maturity
        and info
        and (maturity.year, maturity.month)
        != (
            info.maturity_month.year,
            info.maturity_month.month,
        )
    ):
        notes.append(f"maturity {maturity} contradicts nemo; blanked")
        maturity = None
    return ad, maturity, "; ".join(notes) or None


def load_consolidated(content: bytes, file_year: int | None = None) -> list[AuctionResult]:
    """Rows of one 'Licitaciones YYYY.xlsx' (sheet 'Licitaciones Bonos') as AuctionResults.

    The header row is found by 'Código Nemotécnico'; the allocation split sits one row below
    ('Total' | 'Bcos' | 'AFP, Corredores…'). Raises if the expected columns aren't there.
    """
    sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None)
    name = next((n for n in sheets if "licitaciones" in norm(n)), None)
    if name is None:
        raise ValueError(f"No 'Licitaciones Bonos' sheet (sheets: {list(sheets)})")
    df = sheets[name]
    h = next(
        (
            i
            for i, r in df.iterrows()
            if any(norm(v).startswith("codigo nemotecnico") for v in r.tolist())
        ),
        None,
    )
    if h is None:
        raise ValueError("Consolidated sheet: header row 'Código Nemotécnico' not found")
    cols: dict[str, int] = {}
    for j, v in enumerate(df.iloc[h].tolist()):
        n = norm(v)
        for frag, fld in CONS_COLS.items():
            if n.startswith(frag) and fld not in cols:
                cols[fld] = j
    sub = [norm(v) for v in df.iloc[h + 1].tolist()]
    for j, n in enumerate(sub):
        if n.startswith("bcos") or n.startswith("bancos"):
            cols["banks"] = j
        elif n.startswith("afp"):
            cols["afp_ins_brokers"] = j
    need = {"nemo", "auction_date", "cupo", "demand", "allocated", "rate", "banks"}
    if need - cols.keys():
        raise ValueError(f"Consolidated sheet missing columns {need - cols.keys()}")

    out: list[AuctionResult] = []
    for _, r in df.iloc[h + 2 :].iterrows():
        nemo = r.iloc[cols["nemo"]]
        if not isinstance(nemo, str) or not NEMO_CODE_RE.match(nemo.strip().upper()):
            continue
        nemo = nemo.strip().upper()
        ad = _excel_date(r.iloc[cols["auction_date"]])
        if ad is None:
            raise ValueError(f"Consolidated row {nemo}: no auction date")
        mat = _excel_date(r.iloc[cols["maturity"]]) if "maturity" in cols else None
        ad, mat, note = _check_dates(nemo, ad, mat, file_year)
        vals = {f: r.iloc[cols[f]] for f in ("cupo", "demand", "allocated", "banks", "rate")}
        vals["afp_ins_brokers"] = (
            r.iloc[cols["afp_ins_brokers"]] if "afp_ins_brokers" in cols else None
        )
        void = any(norm(v) == "desierta" for v in vals.values() if isinstance(v, str))
        if not void and parse_number(vals["demand"]) == 0 and not parse_number(vals["allocated"]):
            void = True  # no bids: the file writes zeros (rate 0 is not a clearing rate)
            note = "; ".join(x for x in (note, "no bids (zeros in file)") if x)
        num = {k: (None if void and k != "cupo" else parse_number(v)) for k, v in vals.items()}
        cupo, demand = num["cupo"], num["demand"]
        out.append(
            AuctionResult(
                auction_date=ad,
                bond=bond_label(nemo),
                nemo=nemo,
                maturity=mat,
                unit=_result_unit(str(r.iloc[cols["unit"]]) if "unit" in cols else None, nemo),
                cupo=cupo,
                demand=demand,
                allocated=num["allocated"],
                banks=num["banks"],
                afp_ins_brokers=num["afp_ins_brokers"],
                rate=num["rate"],
                bid_to_cover=round(demand / cupo, 3) if (demand and cupo) else None,
                status="desierta" if void else "ok",
                instrument_type=instrument_type(None, nemo),
                note=note,
            )
        )
    if not out:
        raise ValueError("Consolidated sheet parsed but contained no auction rows")
    return _dedupe(out)


def _dedupe(rows: list[AuctionResult]) -> list[AuctionResult]:
    """One row per (auction_date, nemo); exact duplicates dropped, conflicting ones raise."""
    seen: dict[tuple[date, str], AuctionResult] = {}
    for r in rows:
        k = (r.auction_date, r.nemo)
        if k in seen and seen[k] != r:
            raise ValueError(f"Conflicting consolidated rows for {k}")
        seen[k] = r
    return list(seen.values())


def load_stock(content: bytes, as_of: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """'Stock Total' workbook → (master rows, outstanding rows) for local bonds (Pesos / UF).

    Coupons are stored either as a fraction (0.049) or in % (5.8); the scaling is chosen by
    matching the coupon encoded in the nemo. Extra cells beside the table are ignored.
    """
    df = pd.read_excel(io.BytesIO(content), sheet_name=0, header=None)
    h = next(
        (i for i, r in df.iterrows() if any("mnemotecnico" in norm(v) for v in r.tolist())), None
    )
    if h is None:
        raise ValueError("Stock sheet: header with 'Código Mnemotécnico' not found")
    head = [norm(v) for v in df.iloc[h].tolist()]

    def col(frag: str) -> int:
        for j, n in enumerate(head):
            if n.startswith(frag):
                return j
        raise ValueError(f"Stock sheet: column '{frag}' not found in {head}")

    c = {
        k: col(f)
        for k, f in {
            "currency": "moneda",
            "isin": "isin",
            "nemo": "codigo mnemotecnico",
            "issue": "fecha de emision",
            "maturity": "fecha vencimiento",
            "coupon": "cupon",
            "amount": "monto vigente",
            "unit": "unidad",
            "esg": "caracteristica esg",
        }.items()
    }
    master, outstanding = [], []
    for _, r in df.iloc[h + 1 :].iterrows():
        cur = norm(r.iloc[c["currency"]])
        if cur not in ("pesos", "uf"):
            continue
        nemo = str(r.iloc[c["nemo"]]).strip().upper()
        info = decode_nemo(nemo)
        if info is None:
            raise ValueError(f"Stock sheet: unexpected local nemo {nemo!r}")
        raw_cpn = parse_number(r.iloc[c["coupon"]]) or 0.0
        cpn = min((raw_cpn, raw_cpn * 100), key=lambda x: abs(x - info.coupon_pct))
        maturity = _excel_date(r.iloc[c["maturity"]])
        master.append(
            {
                "nemo": nemo,
                "isin": str(r.iloc[c["isin"]]).strip(),
                "instrument_type": info.instrument_type,
                "currency": "CLP" if cur == "pesos" else "UF",
                "coupon_pct": round(cpn, 4),
                "issue_date": _excel_date(r.iloc[c["issue"]]),
                "maturity_date": maturity,
                "esg": _clean(r.iloc[c["esg"]]),
            }
        )
        amt = parse_number(r.iloc[c["amount"]])
        if amt is not None:
            outstanding.append(
                {
                    "nemo": nemo,
                    "as_of": as_of,
                    "amount": amt,
                    "unit": "MM CLP" if cur == "pesos" else "M UF",
                }
            )
    if not master:
        raise ValueError("Stock sheet: no local (Pesos/UF) bonds found")
    return pd.DataFrame(master), pd.DataFrame(outstanding)


def _clean(v: Any) -> str | None:
    s = None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
    return None if not s or norm(s) in ("n.a.", "na", "nan") else s


def _coupon_months(text: str) -> list[int] | None:
    """'mar/sep', '15-Abr / 15-Oct', '01-Mar/ 01-Sept' -> [3, 9]; 'n.a.' -> []."""
    t = norm(text)
    if not t or t.startswith("n.a") or t == "na":
        return []
    months = [
        SPANISH_MONTHS[m] for m in re.findall(r"(?<![a-z])([a-z]{3})", t) if m in SPANISH_MONTHS
    ]
    return sorted(set(months)) or None


def parse_characteristics(html: str) -> pd.DataFrame:
    """'Características Financieras - Bonos Locales' → [nemo, issue_date, maturity_date,
    coupon_pct, coupon_months, status] for the tables that list nemos (older tables use
    generic names like 'BTU-20 (UF)' and are skipped)."""
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for t in soup.find_all("table"):
        g = table_grid(t)
        name_row = next(
            (r for r in g if sum(bool(NEMO_CODE_RE.match(c.strip())) for c in r) > 0), None
        )
        if name_row is None:
            continue
        labels = {norm(r[0]): r for r in g}

        def row(prefix: str, labels: dict = labels) -> list[str] | None:
            return next((v for k, v in labels.items() if k.startswith(prefix)), None)

        issue, mat, pays = row("fecha emision"), row("fecha vencimiento"), row("paga")
        rate, status = row("tasa caratula"), row("status")
        for j, cell in enumerate(name_row):
            nemo = cell.strip().upper()
            if not NEMO_CODE_RE.match(nemo):
                continue
            rows.append(
                {
                    "nemo": nemo,
                    "issue_date": parse_date(issue[j]) if issue else None,
                    "maturity_date": parse_date(mat[j]) if mat else None,
                    "coupon_pct": parse_number(rate[j]) if rate else None,
                    "coupon_months": _coupon_months(pays[j]) if pays else None,
                    "status": status[j] if status else None,
                }
            )
    if not rows:
        raise ValueError("Características: no tables with nemos found. Page layout changed?")
    df = pd.DataFrame(rows)
    return df.drop_duplicates("nemo", keep="last").reset_index(drop=True)


def build_master(stock: pd.DataFrame, chars: pd.DataFrame, extra_nemos: list[str]) -> pd.DataFrame:
    """Bond master: Stock Total (live bonds) > Características (incl. matured) > nemo rule.

    Coupon months come from Características; otherwise they're inferred from the maturity
    month (semiannual) and flagged. A coupon-month list that doesn't include the maturity
    month is flagged for review (e.g. Hacienda lists BTP0610456 as 'ene/jul' maturing April).
    """
    ch = chars.set_index("nemo") if not chars.empty else pd.DataFrame()
    recs: dict[str, dict] = {}
    for r in stock.to_dict("records"):
        recs[r["nemo"]] = {**r, "source": "stock"}
    for nemo, r in ch.iterrows() if not ch.empty else []:
        if nemo in recs:
            continue
        info = decode_nemo(nemo)
        if info is None:
            continue
        recs[nemo] = {
            "nemo": nemo,
            "isin": None,
            "instrument_type": info.instrument_type,
            "currency": info.currency,
            "coupon_pct": r["coupon_pct"],
            "issue_date": r["issue_date"],
            "maturity_date": r["maturity_date"],
            "esg": None,
            "source": "characteristics",
        }
    for nemo in sorted(set(extra_nemos) - set(recs)):
        info = decode_nemo(nemo)
        if info is None:
            continue
        recs[nemo] = {
            "nemo": nemo,
            "isin": None,
            "instrument_type": info.instrument_type,
            "currency": info.currency,
            "coupon_pct": info.coupon_pct,
            "issue_date": None,
            "maturity_date": info.maturity_month,
            "esg": None,
            "source": "nemo",
        }
    out = []
    for nemo, r in recs.items():
        flags = []
        cpn = float(r["coupon_pct"] or 0.0)
        months = ch.loc[nemo, "coupon_months"] if (not ch.empty and nemo in ch.index) else None
        mat = r["maturity_date"]
        if cpn == 0:
            months = []
        elif not months:
            if mat is not None:
                months = sorted({mat.month, (mat.month + 5) % 12 + 1})
                flags.append("coupon months inferred from maturity")
        elif mat is not None and mat.month not in months:
            flags.append(f"coupon months {months} exclude maturity month {mat.month}")
        if r["source"] == "nemo":
            flags.append("not in Hacienda master: decoded from nemo (maturity day unknown)")
        info = decode_nemo(nemo)
        if info and abs(info.coupon_pct - cpn) > 0.051:
            flags.append(f"coupon {cpn} differs from nemo {info.coupon_pct}")
        out.append(
            {
                **r,
                "coupon_freq": 2 if cpn > 0 else 0,
                "coupon_months": ",".join(str(m) for m in months) if months else None,
                "amortization": "bullet",
                "day_count": "ACT/365",
                "flagged": "; ".join(flags) or None,
            }
        )
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- fetching


def _now() -> datetime:
    """Storage timestamp: naive UTC (repo convention). Tests monkeypatch this."""
    return storage.utcnow()


def santiago_today(now_utc: datetime | None = None) -> date:
    now_utc = now_utc or _now()
    return now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(SANTIAGO).date()


def fetch(url: str, kind: str, client: HttpClient | None = None) -> bytes:
    """GET via the shared client (retries, rate limit, User-Agent); raw bytes saved under
    data/raw/hacienda/<date>/."""
    own = client is None
    client = client or HttpClient()
    try:
        resp = client.get(url)
    finally:
        if own:
            client.__exit__()
    ext = "xlsx" if "spreadsheet" in resp.headers.get("content-type", "") else "html"
    storage.save_raw("hacienda", f"{_now():%H%M%S}_{kind}.{ext}", resp.content)
    return resp.content


def _html(b: bytes) -> str:
    return b.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- storage wrappers


def store_calendar(con: duckdb.DuckDBPyConnection, rows: list[CalendarRow]) -> list[dict]:
    """Upsert calendar rows; mark future rows that vanished as removed. Returns change list."""
    recs = [{**asdict(r), "instrument_type": r.instrument_type} for r in rows]
    return storage.upsert_auction_calendar(con, recs, santiago_today(), _now())


def store_results(
    con: duckdb.DuckDBPyConnection, results: list[AuctionResult], source: str
) -> tuple[int, int]:
    """Insert new results; if an existing one changed, update it and log a revision.
    Returns (new, revised). Consolidated rows never overwrite live ones."""
    new, rev, _ = storage.upsert_auction_results(con, [r.record() for r in results], source, _now())
    return new, rev


# --------------------------------------------------------------------------- jobs


def run_calendar(con: duckdb.DuckDBPyConnection) -> list[dict]:
    with storage.ingest_run(con, "hacienda.calendar", "hacienda") as run:
        rows = parse_calendar(_html(fetch(URL_CALENDAR, "calendar")))
        changes = store_calendar(con, rows)
        run.rows = len(changes)
    return changes


def run_results(con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    errors = []
    with storage.ingest_run(con, "hacienda.results", "hacienda") as run:
        for url in (URL_RESULTS, URL_RESULTS_ALT):
            try:
                new, rev = store_results(con, parse_results(_html(fetch(url, "results"))), "live")
                run.rows = new + rev
                return new, rev
            except (HttpError, ValueError) as e:
                errors.append(f"{url.rsplit('/', 1)[-1]}: {e}")
        raise RuntimeError("Results scrape failed on both pages: " + " | ".join(errors))


def run_soma(con: duckdb.DuckDBPyConnection) -> SomaStatus:
    """Store authorized / placed (USD mm) as dated observations (vintages build up daily)."""
    with storage.ingest_run(con, "hacienda.soma", "hacienda") as run:
        s = parse_soma(_html(fetch(URL_SOMA, "soma")))
        specs = [
            SeriesSpec(
                series_id=f"hacienda_auth_usd_{s.year}",
                source="hacienda",
                source_code="SOMA page text",
                name=f"Authorized Treasury bond issuance {s.year}",
                freq="D",
                unit="USD mm",
                tab="bond_monitor",
                verified=True,
            ),
            SeriesSpec(
                series_id=f"hacienda_placed_usd_{s.year}",
                source="hacienda",
                source_code="SOMA page text",
                name=f"Cumulative bonds placed {s.year}",
                freq="D",
                unit="USD mm",
                tab="bond_monitor",
                verified=True,
            ),
        ]
        storage.upsert_series_meta(con, specs)
        today = santiago_today()
        df = pd.DataFrame(
            {
                "series_id": [specs[0].series_id, specs[1].series_id],
                "obs_date": [today, today],
                "value": [s.authorized_usd_mm, s.placed_usd_mm],
            }
        )
        run.rows = storage.upsert_observations(con, df, "hacienda")
    return s


def run_master(con: duckdb.DuckDBPyConnection) -> int:
    """Bond master + outstanding from Stock Total and Características Financieras."""
    with storage.ingest_run(con, "hacienda.master", "hacienda") as run, HttpClient() as client:
        stock_master, outstanding = load_stock(fetch(URL_STOCK, "stock", client), santiago_today())
        chars = parse_characteristics(_html(fetch(URL_CHARACTERISTICS, "characteristics", client)))
        seen = [
            r[0]
            for r in con.execute(
                "SELECT nemo FROM auction_results UNION SELECT nemo FROM auction_calendar "
                "WHERE nemo IS NOT NULL"
            ).fetchall()
        ]
        master = build_master(stock_master, chars, seen)
        run.rows = storage.replace_bond_master(con, master)
        storage.upsert_bond_outstanding(con, outstanding)
    return run.rows


def backfill(con: duckdb.DuckDBPyConnection, years: list[int] | None = None) -> dict[int, tuple]:
    """Load 'Resultados Licitaciones YYYY (consolidado)' files (source='consolidated')."""
    out: dict[int, tuple] = {}
    with HttpClient() as client:
        links = consolidated_links(_html(fetch(URL_STATS, "estadisticas", client)))
        for y in sorted(years or links):
            if y not in links:
                out[y] = ("error", "no link on Estadísticas")
                continue
            with storage.ingest_run(con, f"hacienda.backfill_{y}", "hacienda") as run:
                rows = load_consolidated(fetch(links[y], f"consolidado_{y}", client), y)
                new, rev, skipped = storage.upsert_auction_results(
                    con, [r.record() for r in rows], "consolidated", _now()
                )
                run.rows = new + rev
                out[y] = (len(rows), new, rev, skipped)
    return out


def missing_result_dates(con: duckdb.DuckDBPyConnection, today: date | None = None) -> list[date]:
    """Auction dates in the stored calendar that are <= today but have no stored result."""
    return storage.auction_missing_result_dates(con, today or santiago_today())


REFRESH_MIN_INTERVAL = timedelta(minutes=30)


def _recoverable(con: duckdb.DuckDBPyConnection, missing: list[date]) -> list[date]:
    """Missing dates the live page can still show: it only carries the latest auction, so a
    gap older than the newest stored result waits for the consolidated file instead."""
    latest = storage._first(con, "SELECT max(auction_date) FROM auction_results")
    if latest is None:
        return missing
    latest = latest.date() if isinstance(latest, datetime) else latest
    return [d for d in missing if d > latest]


def _recently_tried(con: duckdb.DuckDBPyConnection) -> bool:
    last = storage._first(
        con, "SELECT max(started_at) FROM ingest_log WHERE job = 'hacienda.results'"
    )
    return last is not None and _now() - last < REFRESH_MIN_INTERVAL


def get_results(con: duckdb.DuckDBPyConnection, refresh_if_stale: bool = True) -> pd.DataFrame:
    """What the dashboard calls. Scrapes first if a past auction's result is missing (and the
    live page could still have it; at most every 30 minutes)."""
    if refresh_if_stale:
        todo = _recoverable(con, missing_result_dates(con))
        if todo and not _recently_tried(con):
            try:
                run_results(con)
            except Exception as e:  # noqa: BLE001  (logged in ingest_log; serve stored data)
                log.warning("refresh failed, serving stored data: %s", e)
    return storage.read_auction_results(con)


def watch(
    connect=storage.connect,
    start_local: str = "11:05",
    end_local: str = "18:00",
    every_min: int = 15,
) -> str:
    """On an auction day (per the stored calendar), poll the results page until today's result
    is stored. Exits at once on other days. Opens the database only for each poll (DuckDB allows
    one writer process; the dashboard and other jobs must not be locked out for hours)."""
    today = santiago_today()
    con = connect()
    try:
        if not storage.count_calendar_on(con, today):
            return f"{today}: no auction scheduled. Nothing to watch."
    finally:
        con.close()
    t0 = datetime.combine(today, datetime.strptime(start_local, "%H:%M").time(), SANTIAGO)
    t1 = datetime.combine(today, datetime.strptime(end_local, "%H:%M").time(), SANTIAGO)
    while datetime.now(SANTIAGO) < t1:
        if datetime.now(SANTIAGO) >= t0:
            con = connect()
            try:
                try:
                    run_results(con)
                except Exception as e:  # noqa: BLE001
                    log.warning("watch: %s", e)
                if storage.count_results_on(con, today):
                    return f"{today}: results stored."
            finally:
                con.close()
        time.sleep(every_min * 60)
    return f"{today}: results not published by {end_local} Santiago. The evening job will retry."


# --------------------------------------------------------------------------- CLI helpers


def debug() -> None:
    """First-run check: dump every table the scraper sees, and what it parses."""
    for name, url, parser in [
        ("calendar", URL_CALENDAR, parse_calendar),
        ("results", URL_RESULTS, parse_results),
        ("results_alt", URL_RESULTS_ALT, parse_results),
    ]:
        print(f"\n================ {name}: {url}")
        html = _html(fetch(url, f"debug_{name}"))
        for i, t in enumerate(BeautifulSoup(html, "lxml").find_all("table")):
            g = table_grid(t)
            print(f"\n-- table {i}: {len(g)} rows")
            for row in g[:8]:
                print("   ", row)
        try:
            parsed = parser(html)
            print(f"\nPARSED {len(parsed)} rows:")
            for p in parsed:
                print("   ", p)
        except Exception as e:  # noqa: BLE001
            print(f"\nPARSER FAILED: {e!r}")


def show(con: duckdb.DuckDBPyConnection) -> None:
    cal = storage.read_auction_calendar(con)
    print("\nCalendar:")
    print(
        cal[["auction_date", "instrument", "nemo", "unit", "amount", "status"]].to_string(
            index=False
        )
    )
    res = storage.read_auction_results(con).head(20)
    print("\nLatest stored results:")
    cols = [
        "auction_date",
        "bond",
        "nemo",
        "cupo",
        "demand",
        "allocated",
        "bid_to_cover",
        "rate",
        "status",
        "source",
    ]
    print(res[cols].to_string(index=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Hacienda Treasury auction scraper")
    ap.add_argument(
        "cmd",
        choices=[
            "calendar",
            "results",
            "all",
            "soma",
            "master",
            "watch",
            "backfill",
            "show",
            "debug",
        ],
    )
    ap.add_argument("--year", type=int, action="append", help="backfill only these years")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if a.cmd == "debug":
        debug()
        return 0
    con = storage.connect()
    try:
        if a.cmd in ("calendar", "all"):
            changes = run_calendar(con)
            for c in changes:
                print(
                    f"CALENDAR {c['change'].upper():10s} {c['auction_date']} "
                    f"{c['instrument']:16s} {c['old']} -> {c['new']}"
                )
            if not changes:
                print("Calendar: no changes.")
        if a.cmd in ("results", "all"):
            new, rev = run_results(con)
            print(f"Results: {new} new, {rev} revised.")
            if miss := missing_result_dates(con):
                print(f"WARNING: no stored result for past auction dates: {miss}")
        if a.cmd in ("soma", "all"):
            s = run_soma(con)
            print(f"SOMA {s.year}: USD {s.placed_usd_mm:,.0f}mm of {s.authorized_usd_mm:,.0f}mm")
        if a.cmd == "master":
            print(f"Bond master: {run_master(con)} bonds")
        if a.cmd == "watch":
            con.close()
            print(watch())
        if a.cmd == "backfill":
            for y, res in backfill(con, a.year).items():
                print(f"{y}: {res}")
        if a.cmd == "show":
            show(con)
    finally:
        con.close()
    return 0


class HaciendaSource(Source):
    name = "hacienda"

    def fetch(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        rows = parse_calendar(_html(fetch(URL_CALENDAR, "calendar")))
        return pd.DataFrame([{**asdict(r), "instrument_type": r.instrument_type} for r in rows])

    def health_check(self) -> HealthResult:
        try:
            with HttpClient() as client:
                rows = parse_calendar(_html(client.get(URL_CALENDAR).content))
        except (HttpError, ValueError) as e:
            return HealthResult(False, str(e), "fail")
        nxt = min((r.auction_date for r in rows), default=None)
        return HealthResult(True, f"calendar: {len(rows)} auctions, first {nxt}", "ok")


if __name__ == "__main__":
    sys.exit(main())
