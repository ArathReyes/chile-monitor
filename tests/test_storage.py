from datetime import date, datetime

import pandas as pd
import pytest

from monitor import storage
from monitor.config import load_series
from monitor.health import is_stale

TABLES = {
    "series_meta",
    "observations",
    "observations_history",
    "market_snapshots",
    "documents",
    "auctions",
    "ingest_log",
}


@pytest.fixture
def conn():
    c = storage.connect(":memory:")
    yield c
    c.close()


def _obs(*rows):
    return pd.DataFrame(rows, columns=["series_id", "obs_date", "value"])


def test_schema_created(conn):
    names = {
        r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    assert names >= TABLES


def test_upsert_and_vintages(conn):
    t1, t2 = datetime(2026, 1, 1), datetime(2026, 2, 1)
    n = storage.upsert_observations(
        conn,
        _obs(("cl_cpi", date(2025, 12, 1), 100.0), ("cl_cpi", date(2026, 1, 1), 100.5)),
        "bcch",
        t1,
    )
    assert n == 2
    # Re-send: one unchanged, one revised, one new
    n = storage.upsert_observations(
        conn,
        _obs(
            ("cl_cpi", date(2025, 12, 1), 100.0),
            ("cl_cpi", date(2026, 1, 1), 100.6),
            ("cl_cpi", date(2026, 2, 1), 101.0),
        ),
        "bcch",
        t2,
    )
    assert n == 2
    cur = storage.read_series(conn, "cl_cpi")
    assert cur["value"].tolist() == [100.0, 100.6, 101.0]
    hist = storage.read_vintages(conn, "cl_cpi")
    jan = hist[hist["obs_date"] == pd.Timestamp("2026-01-01")]
    assert jan["value"].tolist() == [100.5, 100.6]
    assert len(hist) == 4


def test_upsert_rejects_nulls_and_dupes(conn):
    with pytest.raises(ValueError, match="nulls"):
        storage.upsert_observations(conn, _obs(("x", date(2026, 1, 1), None)), "s")
    with pytest.raises(ValueError, match="duplicate"):
        storage.upsert_observations(
            conn, _obs(("x", date(2026, 1, 1), 1.0), ("x", date(2026, 1, 1), 2.0)), "s"
        )
    with pytest.raises(ValueError, match="missing columns"):
        storage.upsert_observations(conn, pd.DataFrame({"series_id": ["x"]}), "s")


def test_series_meta_from_registry(conn):
    reg = load_series()
    assert storage.upsert_series_meta(conn, reg.series) == len(reg.series)
    storage.upsert_series_meta(conn, reg.series)  # idempotent
    status = storage.series_status(conn)
    assert len(status) == len(reg.series)
    assert status["n_obs"].sum() == 0


def test_ingest_run_logs_ok_and_error(conn):
    with storage.ingest_run(conn, "job_a", "bcch") as run:
        run.rows = 5
    with pytest.raises(RuntimeError), storage.ingest_run(conn, "job_b", "fred"):
        raise RuntimeError("boom ?api_key=SECRET")
    summary = storage.ingest_summary(conn).set_index("job")
    assert summary.loc["job_a", "last_status"] == "ok"
    assert summary.loc["job_a", "last_rows"] == 5
    assert summary.loc["job_b", "last_status"] == "error"
    errs = storage.recent_errors(conn)
    assert len(errs) == 1 and "SECRET" not in errs["error"].iloc[0]


def test_market_snapshot(conn):
    storage.insert_market_snapshot(conn, "kalshi", "KXCBDCHILE-TEST", {"yes": 0.6})
    assert conn.execute("SELECT count(*) FROM market_snapshots").fetchone()[0] == 1


def test_save_raw(tmp_path):
    path, sha = storage.save_raw("bcch", "x.json", b"{}", raw_dir=tmp_path)
    assert path.read_bytes() == b"{}" and len(sha) == 64


def test_is_stale():
    today = date(2026, 11, 14)
    assert not is_stale(date(2026, 11, 12), "D", today)
    assert is_stale(date(2026, 11, 1), "D", today)
    assert not is_stale(date(2026, 10, 1), "M", today)
    assert is_stale(date(2026, 8, 1), "M", today)
    assert is_stale(None, "M", today)
