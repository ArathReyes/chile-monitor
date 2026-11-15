from datetime import date, datetime

import pytest

from monitor.sources.ine import _reference_end, parse_agenda


def test_parse_agenda_fixture(load_text_fixture):
    df = parse_agenda(load_text_fixture("ine_agenda_2026-11-15.html"), date(2026, 11, 15))
    cpi = df[df.indicator == "cpi"].iloc[0]
    assert cpi.release_local == datetime(2026, 12, 7, 8, 0)
    assert cpi.reference_period == "2026-11"
    emp = df[df.indicator == "unemployment"]
    assert emp.reference_period.tolist() == ["2026-10", "2026-11"]
    assert df[df.indicator == "wages"].reference_period.iloc[0] == "2026-10"


def test_year_rollover():
    html = """<div class="cuadroMes"><h1 class="tituloMesEaV2">diciembre</h1>
    <div class="eventoAgendaV2"><span class="diaea2">07</span><span class="horaea2">08:00</span>
    <span class="tituloea2">Índice de Precios al Consumidor (IPC), período noviembre 2026</span>
    </div></div>
    <div class="cuadroMes"><h1 class="tituloMesEaV2">enero</h1>
    <div class="eventoAgendaV2"><span class="diaea2">08</span><span class="horaea2">08:00</span>
    <span class="tituloea2">Índice de Precios al Consumidor (IPC), período diciembre 2026</span>
    </div></div>"""
    df = parse_agenda(html, date(2026, 12, 1))
    assert df.release_local.tolist() == [datetime(2026, 12, 7, 8), datetime(2027, 1, 8, 8)]


def test_reference_end_and_layout_change():
    assert _reference_end("agosto – octubre 2026") == "2026-10"
    assert _reference_end("noviembre 2026") == "2026-11"
    with pytest.raises(ValueError):
        parse_agenda("<html><body>nothing</body></html>", date(2026, 11, 15))
