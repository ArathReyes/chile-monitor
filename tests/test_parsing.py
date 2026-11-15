from datetime import date

import pytest

from monitor.parsing import parse_bde_date, parse_bde_value, parse_cl_date, parse_cl_number


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4,50", 4.5),
        ("1.234,5", 1234.5),
        ("1.234.567,89", 1234567.89),
        ("-0,25", -0.25),
        ("0,25%", 0.25),
        ("(1,5)", -1.5),
        (" 12,0 ", 12.0),
        ("850", 850.0),
        ("12.345", 12345.0),
    ],
)
def test_parse_cl_number(text, expected):
    assert parse_cl_number(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", [None, "", "-", "n.d.", "ND", "s/i"])
def test_parse_cl_number_missing(text):
    assert parse_cl_number(text) is None


@pytest.mark.parametrize("text", ["4.50", "abc", "1,2,3", "1.23,4"])
def test_parse_cl_number_rejects(text):
    with pytest.raises(ValueError):
        parse_cl_number(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("02.Ene.2026", date(2026, 1, 2)),
        ("15-Sep-2025", date(2025, 9, 15)),
        ("1.Dic.2025", date(2025, 12, 1)),
        ("30.Sept.2025", date(2025, 9, 30)),
        ("2 de enero de 2026", date(2026, 1, 2)),
        ("02-01-2026", date(2026, 1, 2)),
        ("02/01/2026", date(2026, 1, 2)),
        ("2026-01-02", date(2026, 1, 2)),
    ],
)
def test_parse_cl_date(text, expected):
    assert parse_cl_date(text) == expected


@pytest.mark.parametrize("text", ["02.Jan.2026", "2026/01/02", "31.Feb.2026", ""])
def test_parse_cl_date_rejects(text):
    with pytest.raises(ValueError):
        parse_cl_date(text)


def test_bde_value_and_date():
    assert parse_bde_value("1.5") == 1.5
    assert parse_bde_value("NaN") is None
    assert parse_bde_date("12-10-2021") == date(2021, 10, 12)


def test_bcch_parse_get_series(load_fixture):
    from monitor.sources.bcch import parse_get_series

    df = parse_get_series(load_fixture("bcch_getseries_tpm.json"))
    assert list(df.columns) == ["obs_date", "value"]
    assert len(df) == 3  # NaN row dropped, not filled
    assert df["obs_date"].iloc[0] == date(2021, 10, 12)


def test_bcch_parse_drops_placeholder_dates(caplog):
    from monitor.sources.bcch import parse_get_series

    payload = {
        "Codigo": 0,
        "Descripcion": "Success",
        "Series": {
            "seriesId": "F022.SPC.TIN.D090.NO.Z.D",
            "Obs": [
                {"indexDateString": "31-12-1899", "value": "0", "statusCode": "OK"},
                {"indexDateString": "13-11-2026", "value": "4.32", "statusCode": "OK"},
            ],
        },
    }
    with caplog.at_level("WARNING", logger="monitor.sources.bcch"):
        df = parse_get_series(payload)
    assert df["obs_date"].tolist() == [date(2026, 11, 13)]
    assert df["value"].tolist() == [4.32]
    assert "1899-12-31" in caplog.text


def test_bcch_parse_error_payload():
    from monitor.sources.bcch import BcchApiError, parse_get_series

    with pytest.raises(BcchApiError):
        parse_get_series({"Codigo": -5, "Descripcion": "Invalid token"})


def test_redact():
    from monitor.http import redact

    s = "GET https://x/y?token=$2a$10$abc&function=GetSeries&api_key=zzz"
    out = redact(s)
    assert "abc" not in out and "zzz" not in out
    assert "function=GetSeries" in out
