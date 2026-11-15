"""Hacienda ODP scraper. Part 1: the provided synthetic tests (adapted to the repo's storage).
Part 2: real pages and files saved on 2026-11-15 (tests/fixtures/hacienda/)."""

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from monitor import storage
from monitor.sources import hacienda as h

FIX = Path(__file__).parent / "fixtures" / "hacienda"

CAL_HTML = """
<html><body><h2>Calendario de Licitaciones Cuarto Trimestre 2026</h2>
<table>
 <tr><th rowspan="4">Fecha de operación</th>
     <th colspan="2">Letras de Tesorería en Pesos</th><th colspan="2">BTP</th><th colspan="2">BTU</th></tr>
 <tr><th colspan="2">Millones de Pesos</th><th colspan="2">Millones de Pesos</th><th colspan="2">Miles de UF</th></tr>
 <tr><th>Letra oct. 2027</th><th>Letra nov. 2027</th><th>BTP-2032</th><th>BTP-2038</th>
     <th>BTU-2031</th><th>BTU-2055 (R)</th></tr>
 <tr><td>CL0000000001</td><td>CL0000000002</td><td>CL0000000003 BTP0500332</td><td>CL0000000004</td>
     <td>CL0000000005 BTU0190331</td><td>CL0000000006</td></tr>
 <tr><td>05-nov-26</td><td>-</td><td>50.000</td><td>25.000</td><td>-</td><td>-</td><td>-</td></tr>
 <tr><td>12-nov-26</td><td>50.000</td><td>-</td><td>-</td><td>-</td><td>-</td><td>500</td></tr>
 <tr><td>17-nov-26</td><td>-</td><td>50.000</td><td>-</td><td>10.000</td><td>500</td><td>-</td></tr>
</table></body></html>
"""  # noqa: E501

RES_HTML = """
<html><body><h3>Resultados Última Licitación (12/11/2026)</h3>
<table>
 <tr><th>Fecha de Licitación</th><th>Bono</th><th>Nemo</th><th>Fecha de Vencimiento</th><th>Unidad</th>
     <th>Cupo</th><th>Monto Demandado</th><th>Total Adjudicado</th><th>Bancos</th>
     <th>AFP, Cías. Seguros y Corredoras</th><th>Tasa de Interés</th></tr>
 <tr><td>12/11/2026</td><td>Letra oct. 2027</td><td>BTP0001027</td><td>15/10/2027</td><td>Millones de Pesos</td>
     <td>50.000</td><td>140.000</td><td>50.000</td><td>45.000</td><td>5.000</td><td>4,62%</td></tr>
 <tr><td>12/11/2026</td><td>BTU-2055 (R)</td><td>BTU0280155</td><td>01/01/2055</td><td>Miles de UF</td>
     <td>500</td><td>1.510</td><td>500</td><td>-</td><td>500</td><td>2,81%</td></tr>
</table></body></html>
"""  # noqa: E501


def _rows(con, sql):
    return con.execute(sql).fetchall()  # never fetchone() with DuckDB 2.x


# ================================================================ part 1: synthetic
@pytest.mark.parametrize(
    "s,exp",
    [
        ("50.000", 50000),
        ("2,81%", 2.81),
        ("-", None),
        ("1.510", 1510),
        ("4,5", 4.5),
        ("", None),
        ("1.234.567,8", 1234567.8),
        ("500", 500),
    ],
)
def test_parse_number(s, exp):
    assert h.parse_number(s) == exp


@pytest.mark.parametrize(
    "s,exp",
    [
        ("05-nov-26", date(2026, 11, 5)),
        ("12/11/2026", date(2026, 11, 12)),
        ("5-dic-2026", date(2026, 12, 5)),
        ("2026-11-12", date(2026, 11, 12)),
        ("01-sept-30", date(2030, 9, 1)),
        ("Noviembre 2", None),
    ],
)
def test_parse_date(s, exp):
    assert h.parse_date(s) == exp


def test_parse_calendar():
    rows = h.parse_calendar(CAL_HTML)
    got = {(r.auction_date, r.instrument): r for r in rows}
    assert len(rows) == 7
    r = got[(date(2026, 11, 12), "BTU-2055 (R)")]
    assert r.amount == 500 and r.unit == "M UF" and r.reopening and r.isin == "CL0000000006"
    r = got[(date(2026, 11, 5), "BTP-2032")]
    assert r.amount == 25000 and r.unit == "MM CLP" and r.nemo == "BTP0500332"
    assert got[(date(2026, 11, 17), "BTU-2031")].unit == "M UF"


def test_parse_results():
    res = h.parse_results(RES_HTML)
    assert len(res) == 2
    btu = next(r for r in res if r.nemo == "BTU0280155")
    assert btu.auction_date == date(2026, 11, 12)
    assert btu.rate == 2.81 and btu.demand == 1510 and btu.bid_to_cover == pytest.approx(3.02)
    assert btu.banks is None and btu.afp_ins_brokers == 500


def test_layout_change_fails_loudly():
    with pytest.raises(ValueError):
        h.parse_calendar("<table><tr><td>nothing</td></tr></table>")
    with pytest.raises(ValueError):
        h.parse_results("<table><tr><td>nothing</td></tr></table>")
    with pytest.raises(ValueError):
        h.parse_soma("<p>no authorization text</p>")


@pytest.fixture
def con():
    c = storage.connect(":memory:")
    yield c
    c.close()


def test_results_accumulate_and_revise(con):
    res = h.parse_results(RES_HTML)
    assert h.store_results(con, res, "live") == (2, 0)
    assert h.store_results(con, res, "live") == (0, 0)  # idempotent
    changed = [
        r.__class__(**{**r.__dict__, "rate": 2.85}) if r.nemo == "BTU0280155" else r for r in res
    ]
    assert h.store_results(con, changed, "live") == (0, 1)  # revision logged
    assert _rows(con, "SELECT count(*) FROM auction_results_revisions")[0][0] == 1
    assert _rows(con, "SELECT rate FROM auction_results WHERE nemo='BTU0280155'")[0][0] == 2.85


def test_calendar_changes(con, monkeypatch):
    monkeypatch.setattr(h, "_now", lambda: datetime(2026, 11, 1, 9, 0))
    rows = h.parse_calendar(CAL_HTML)
    ch = h.store_calendar(con, rows)
    assert len(ch) == 7 and all(c["change"] == "new" for c in ch)
    # Hacienda cuts BTP-2038 on 17-nov and changes BTP-2032 size
    mod = [r for r in rows if r.instrument != "BTP-2038"]
    mod = [
        r.__class__(**{**r.__dict__, "amount": 30000}) if r.instrument == "BTP-2032" else r
        for r in mod
    ]
    ch = h.store_calendar(con, mod)
    kinds = {(c["instrument"], c["change"]) for c in ch}
    assert ("BTP-2038", "removed") in kinds and ("BTP-2032", "amount") in kinds
    # put it back: reinstated
    ch = h.store_calendar(con, rows)
    assert ("BTP-2038", "reinstated") in {(c["instrument"], c["change"]) for c in ch}


def test_missing_results_triggers_refresh(con, monkeypatch):
    monkeypatch.setattr(h, "_now", lambda: datetime(2026, 11, 1, 9, 0))
    h.store_calendar(con, h.parse_calendar(CAL_HTML))
    assert h.missing_result_dates(con, date(2026, 11, 13)) == [
        date(2026, 11, 5),
        date(2026, 11, 12),
    ]
    called = {}

    def fake_run(c):
        called["yes"] = True
        return h.store_results(c, h.parse_results(RES_HTML), "live")

    monkeypatch.setattr(h, "run_results", fake_run)
    monkeypatch.setattr(h, "missing_result_dates", lambda c, today=None: [date(2026, 11, 12)])
    df = h.get_results(con)
    assert called and len(df) == 2


# ================================================================ part 2: real fixtures


def _html(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_real_calendar():
    rows = h.parse_calendar(_html("calendar_2026-11-15.html"))
    assert len(rows) == 12
    assert {r.auction_date for r in rows} == {date(2026, 11, d) for d in (5, 12, 17, 19, 24)}
    btu = next(r for r in rows if r.auction_date == date(2026, 11, 24) and r.nemo == "BTU0281155")
    assert btu.reopening and btu.unit == "M UF" and btu.amount == 500
    assert btu.maturity == date(2055, 11, 1) and btu.isin == "CL0002948918"
    letra = next(r for r in rows if r.instrument == "Letra oct. 2027")
    assert letra.instrument_type == "Letra" and letra.nemo == "BTP0001027"
    assert letra.unit == "MM CLP" and letra.amount == 50000
    assert {r.instrument_type for r in rows} == {"Letra", "BTP", "BTU"}


def test_real_results_both_pages():
    for page in ("results_2026-11-15.html", "soma_2026-11-15.html"):
        res = {r.nemo: r for r in h.parse_results(_html(page))}
        assert set(res) == {"BTP0001027", "BTU0281155"}
        letra, btu = res["BTP0001027"], res["BTU0281155"]
        assert letra.auction_date == date(2026, 11, 12) and letra.instrument_type == "Letra"
        assert (letra.cupo, letra.demand, letra.allocated, letra.rate) == (
            50000,
            245000,
            50000,
            4.28,
        )
        assert letra.bid_to_cover == pytest.approx(4.9)
        assert btu.unit == "M UF" and btu.rate == 2.86 and btu.afp_ins_brokers == 500


def test_real_soma_text():
    s = h.parse_soma(_html("soma_2026-11-15.html"))
    assert s.year == 2026 and s.authorized_usd_mm == 23600 and 0 < s.placed_usd_mm <= 23600


def test_real_statistics_links():
    links = h.consolidated_links(_html("estadisticas_2026-11-15.html"))
    assert set(links) == set(range(2021, 2027))
    assert all(u.startswith("https://www.hacienda.cl/") for u in links.values())


def test_consolidated_loader_and_fixes():
    rows = h.load_consolidated((FIX / "licitaciones_2026.xlsx").read_bytes(), 2026)
    assert len(rows) == 214
    assert {r.auction_date.year for r in rows} == {2026}
    void = [r for r in rows if r.status == "desierta"]
    assert len(void) == 6 and all(r.allocated is None and r.cupo for r in void)
    first = next(r for r in rows if r.nemo == "BTP0001126")
    assert first.bid_to_cover == pytest.approx(1323000 / 350000, abs=1e-3)
    # wrong-century maturities (2031 stored as 1931) are corrected
    assert all(r.maturity is None or r.maturity.year >= 2026 for r in rows)
    y23 = h.load_consolidated((FIX / "licitaciones_2023.xlsx").read_bytes(), 2023)
    fixed = [r for r in y23 if r.note and "outside file year" in r.note]
    assert len(fixed) == 6 and all(r.auction_date.year == 2023 for r in y23)
    y24 = h.load_consolidated((FIX / "licitaciones_2024.xlsx").read_bytes(), 2024)
    bad = [r for r in y24 if r.note and "contradicts nemo" in r.note]
    assert [r.nemo for r in bad] == ["BTP0470930"] and bad[0].maturity is None


def test_live_rows_win_over_consolidated(con):
    live = h.parse_results(_html("results_2026-11-15.html"))
    h.store_results(con, live, "live")
    cons = [r.__class__(**{**r.__dict__, "rate": 9.99}) for r in live]
    new, rev, skipped = storage.upsert_auction_results(
        con, [r.record() for r in cons], "consolidated"
    )
    assert (new, rev, skipped) == (0, 0, 2)
    assert {x[0] for x in _rows(con, "SELECT rate FROM auction_results")} == {4.28, 2.86}


def test_refresh_only_when_live_page_can_help(con, monkeypatch):
    monkeypatch.setattr(h, "_now", lambda: datetime(2026, 11, 16, 15, 0))
    h.store_calendar(con, h.parse_calendar(_html("calendar_2026-11-15.html")))
    h.store_results(con, h.parse_results(_html("results_2026-11-15.html")), "live")
    # 05-Nov is missing but older than the stored 12-Nov result: the live page can't have it
    assert h.missing_result_dates(con, date(2026, 11, 16)) == [date(2026, 11, 5)]
    calls = []
    monkeypatch.setattr(h, "run_results", lambda c: calls.append(1))
    h.get_results(con)
    assert calls == []


def test_nemo_rule_matches_master():
    """decode_nemo vs every local bond in Stock Total and Características Financieras."""
    stock, outstanding = h.load_stock(
        (FIX / "stock_total_2026-11-15.xlsx").read_bytes(), date(2026, 11, 15)
    )
    chars = h.parse_characteristics(_html("caracteristicas_2026-11-15.html"))
    assert len(stock) >= 40 and len(chars) >= 20
    for df in (stock, chars):
        for r in df.itertuples():
            info = h.decode_nemo(r.nemo)
            assert info is not None, r.nemo
            assert info.coupon_pct == pytest.approx(r.coupon_pct, abs=0.051), r.nemo
            assert (info.maturity_month.year, info.maturity_month.month) == (
                r.maturity_date.year,
                r.maturity_date.month,
            ), r.nemo
    assert set(outstanding["unit"]) == {"MM CLP", "M UF"}
    assert h.decode_nemo("BTP0530732").coupon_pct == 5.3
    assert h.decode_nemo("BTP0001027").instrument_type == "Letra"
    assert h.decode_nemo("BTU0001028").instrument_type == "BTU"
    assert h.decode_nemo("XYZ") is None and h.decode_nemo("BTP0001327") is None


def test_build_master_flags():
    stock, _ = h.load_stock((FIX / "stock_total_2026-11-15.xlsx").read_bytes(), date(2026, 11, 15))
    chars = h.parse_characteristics(_html("caracteristicas_2026-11-15.html"))
    m = h.build_master(stock, chars, ["BTP0400399"]).set_index("nemo")
    assert m.loc["BTP0500335", "coupon_months"] == "3,9" and m.loc["BTP0500335", "coupon_freq"] == 2
    assert m.loc["BTP0001027", "coupon_freq"] == 0 and pd.isna(m.loc["BTP0001027", "coupon_months"])
    assert "exclude maturity month" in m.loc["BTP0610456", "flagged"]  # Hacienda lists ene/jul
    assert "decoded from nemo" in m.loc["BTP0400399", "flagged"]
    assert m.loc["BTU0281155", "currency"] == "UF" and m.loc["BTU0281155", "source"] == "stock"


def test_storage_master_roundtrip(con):
    stock, out = h.load_stock(
        (FIX / "stock_total_2026-11-15.xlsx").read_bytes(), date(2026, 11, 15)
    )
    chars = h.parse_characteristics(_html("caracteristicas_2026-11-15.html"))
    n = storage.replace_bond_master(con, h.build_master(stock, chars, []))
    assert n == len(storage.read_bond_master(con)) and n >= 48
    storage.upsert_bond_outstanding(con, out)
    storage.upsert_bond_outstanding(con, out)  # idempotent
    assert len(storage.read_bond_outstanding(con)) == len(out)
