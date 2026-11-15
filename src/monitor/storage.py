"""DuckDB storage: the only module that touches the database.

Timestamps are stored as naive TIMESTAMP values in **UTC** (columns ending in ``_at``/``_ts``).
Convert to America/New_York or America/Santiago only at display time.
"""

from __future__ import annotations

import hashlib
import json
import time
import traceback
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from monitor.config import DATA_DIR, SeriesSpec
from monitor.http import redact

DEFAULT_DB_PATH = DATA_DIR / "monitor.duckdb"
RAW_DIR = DATA_DIR / "raw"

OBS_COLUMNS = ["series_id", "obs_date", "value"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS series_meta (
    series_id   VARCHAR PRIMARY KEY,
    source      VARCHAR NOT NULL,
    source_code VARCHAR NOT NULL,
    name        VARCHAR,
    freq        VARCHAR,
    unit        VARCHAR,
    tab         VARCHAR,
    transform   VARCHAR,
    notes       VARCHAR
);
CREATE TABLE IF NOT EXISTS observations (
    series_id  VARCHAR NOT NULL,
    obs_date   DATE NOT NULL,
    value      DOUBLE NOT NULL,
    source     VARCHAR NOT NULL,
    fetched_at TIMESTAMP NOT NULL,
    PRIMARY KEY (series_id, obs_date)
);
CREATE TABLE IF NOT EXISTS observations_history (
    series_id  VARCHAR NOT NULL,
    obs_date   DATE NOT NULL,
    value      DOUBLE NOT NULL,
    source     VARCHAR NOT NULL,
    fetched_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS market_snapshots (
    source       VARCHAR NOT NULL,
    ticker       VARCHAR NOT NULL,
    snapshot_ts  TIMESTAMP NOT NULL,
    payload_json JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    doc_id       VARCHAR PRIMARY KEY,
    kind         VARCHAR NOT NULL,
    institution  VARCHAR NOT NULL,
    meeting_id   VARCHAR,
    published_at TIMESTAMP,
    title        VARCHAR,
    url          VARCHAR,
    raw_path     VARCHAR,
    text         VARCHAR,
    sha256       VARCHAR
);
-- Provisional: finalize columns in the Bond Monitor spec.
-- Treasury auctions (Hacienda ODP; sources/hacienda.py). Append-only: rows are never deleted.
CREATE TABLE IF NOT EXISTS auction_calendar (
    auction_date    DATE NOT NULL,
    instrument      VARCHAR NOT NULL,   -- as labelled by Hacienda: "BTP-2032", "Letra oct. 2027"
    instrument_type VARCHAR,            -- Letra | BTP | BTU
    nemo            VARCHAR,
    isin            VARCHAR,
    maturity        DATE,
    unit            VARCHAR,            -- "MM CLP" | "M UF"
    amount          DOUBLE,
    reopening       BOOLEAN,
    status          VARCHAR,            -- active | removed
    first_seen      TIMESTAMP,
    last_seen       TIMESTAMP,
    PRIMARY KEY (auction_date, instrument)
);
CREATE TABLE IF NOT EXISTS auction_calendar_changes (
    detected_at  TIMESTAMP NOT NULL,
    auction_date DATE NOT NULL,
    instrument   VARCHAR NOT NULL,
    change       VARCHAR NOT NULL,      -- new | amount | removed | reinstated
    old_amount   DOUBLE,
    new_amount   DOUBLE
);
CREATE TABLE IF NOT EXISTS auction_results (
    auction_date    DATE NOT NULL,
    nemo            VARCHAR NOT NULL,
    bond            VARCHAR,
    instrument_type VARCHAR,
    maturity        DATE,
    unit            VARCHAR,
    cupo            DOUBLE,
    demand          DOUBLE,
    allocated       DOUBLE,
    banks           DOUBLE,
    afp_ins_brokers DOUBLE,
    rate            DOUBLE,
    bid_to_cover    DOUBLE,
    status          VARCHAR,            -- ok | desierta (void auction)
    note            VARCHAR,            -- loader fixes (e.g. typo'd dates in consolidated files)
    source          VARCHAR NOT NULL,   -- live | consolidated
    scraped_at      TIMESTAMP NOT NULL,
    PRIMARY KEY (auction_date, nemo)
);
CREATE TABLE IF NOT EXISTS auction_results_revisions (
    detected_at  TIMESTAMP NOT NULL,
    auction_date DATE NOT NULL,
    nemo         VARCHAR NOT NULL,
    field        VARCHAR NOT NULL,
    old_value    VARCHAR,
    new_value    VARCHAR,
    source       VARCHAR
);
CREATE TABLE IF NOT EXISTS bond_master (
    nemo            VARCHAR PRIMARY KEY,
    isin            VARCHAR,
    instrument_type VARCHAR,            -- Letra | BTP | BTU
    currency        VARCHAR,            -- CLP | UF
    coupon_pct      DOUBLE,
    coupon_freq     INTEGER,            -- 2 = semiannual, 0 = zero coupon
    coupon_months   VARCHAR,            -- e.g. "3,9"
    issue_date      DATE,
    maturity_date   DATE,
    amortization    VARCHAR,            -- bullet
    day_count       VARCHAR,
    esg             VARCHAR,
    source          VARCHAR,            -- stock | characteristics | nemo
    flagged         VARCHAR,            -- why the row needs review (NULL = clean)
    updated_at      TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS bond_outstanding (
    nemo   VARCHAR NOT NULL,
    as_of  DATE NOT NULL,
    amount DOUBLE NOT NULL,
    unit   VARCHAR NOT NULL,
    PRIMARY KEY (nemo, as_of)
);
CREATE TABLE IF NOT EXISTS survey_distribution (
    survey       VARCHAR NOT NULL,   -- 'EEE'
    survey_month DATE NOT NULL,
    section_no   VARCHAR,
    section      VARCHAR NOT NULL,
    horizon      VARCHAR NOT NULL,
    median       DOUBLE,
    p10          DOUBLE,
    p90          DOUBLE,
    n            INTEGER,
    doc_sha256   VARCHAR,
    PRIMARY KEY (survey, survey_month, section, horizon)
);
CREATE TABLE IF NOT EXISTS releases (
    indicator        VARCHAR NOT NULL,   -- cpi, unemployment, wages, ... or 'other'
    title            VARCHAR NOT NULL,
    reference_period VARCHAR,            -- YYYY-MM (end of reference period)
    release_local    TIMESTAMP NOT NULL, -- America/Santiago wall time
    source           VARCHAR NOT NULL,
    source_url       VARCHAR,
    fetched_at       TIMESTAMP NOT NULL,
    PRIMARY KEY (source, title, release_local)
);
CREATE TABLE IF NOT EXISTS release_surprises (
    indicator        VARCHAR NOT NULL,
    reference_period VARCHAR NOT NULL,   -- YYYY-MM
    release_date     DATE,
    consensus        DOUBLE,
    consensus_source VARCHAR,
    consensus_date   DATE,
    actual           DOUBLE,
    actual_is_first_print BOOLEAN,
    surprise         DOUBLE,
    zscore           DOUBLE,
    revised          DOUBLE,
    computed_at      TIMESTAMP NOT NULL,
    PRIMARY KEY (indicator, reference_period)
);
-- Per-bond quotes (by nemotécnico) for the Bond Monitor. Empty: no free, public, daily per-bond
-- source was found (spec 03, Phase 0); a feed can be added later without schema changes.
CREATE TABLE IF NOT EXISTS bond_quotes (
    nemo       VARCHAR NOT NULL,
    obs_date   DATE NOT NULL,
    yield      DOUBLE,
    price      DOUBLE,
    volume     DOUBLE,
    source     VARCHAR NOT NULL,
    fetched_at TIMESTAMP NOT NULL,
    PRIMARY KEY (nemo, obs_date, source)
);
-- Daily Nelson-Siegel-Svensson fits per curve (models.curves.fit_nss). Skipped days are stored
-- with params_json NULL and the reason in `note`.
CREATE TABLE IF NOT EXISTS curve_fits (
    curve       VARCHAR NOT NULL,
    obs_date    DATE NOT NULL,
    params_json VARCHAR,
    rmse        DOUBLE,
    n_points    INTEGER NOT NULL,
    note        VARCHAR,
    computed_at TIMESTAMP NOT NULL,
    PRIMARY KEY (curve, obs_date)
);
CREATE TABLE IF NOT EXISTS ingest_log (
    job         VARCHAR NOT NULL,
    source      VARCHAR NOT NULL,
    started_at  TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status      VARCHAR NOT NULL,
    rows        BIGINT,
    error       VARCHAR
);
"""


# Idempotent migrations for databases created before a column existed.
MIGRATIONS = (
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS meeting_id VARCHAR",
    "DROP TABLE IF EXISTS auctions",  # provisional placeholder, replaced by auction_* (spec 04)
    "ALTER TABLE auction_results ADD COLUMN IF NOT EXISTS note VARCHAR",
)


def _migrate_hacienda_log(conn: duckdb.DuckDBPyConnection) -> None:
    """The standalone hacienda.py wrote to hacienda_ingest_log; fold it into ingest_log."""
    exists = _first(
        conn,
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'hacienda_ingest_log'",
    )
    if exists:
        conn.execute(
            """
            INSERT INTO ingest_log
            SELECT 'hacienda.' || job, 'hacienda', started_at, started_at, status, rows, message
            FROM hacienda_ingest_log
            """
        )
        conn.execute("DROP TABLE hacienda_ingest_log")


def _first(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> Any:
    """First column of the first row, reading the result to completion.

    DuckDB 2.x keeps a result open after fetchone(), blocking the next statement on the
    same connection, so never use fetchone() here.
    """
    rows = conn.execute(sql, params or []).fetchall()
    return rows[0][0] if rows else None


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (storage convention)."""
    return datetime.now(UTC).replace(tzinfo=None)


LOCK_RETRIES = 8  # ~25 s in total: another process (job or app) briefly holds the write lock


def _connect_with_retry(target: str, read_only: bool) -> duckdb.DuckDBPyConnection:
    """DuckDB allows one writer process at a time; wait briefly instead of failing at once."""
    for attempt in range(LOCK_RETRIES):
        try:
            return duckdb.connect(target, read_only=read_only)
        except duckdb.IOException as e:
            if "lock" not in str(e).lower() or attempt == LOCK_RETRIES - 1:
                raise
            time.sleep(0.25 * 2**attempt if attempt < 5 else 5.0)
    raise RuntimeError("unreachable")


def connect(path: Path | str | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the database (``:memory:`` allowed) and ensure the schema exists."""
    target = str(path) if path is not None else str(DEFAULT_DB_PATH)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        if read_only and not Path(target).exists():
            init = duckdb.connect(target)
            init.execute(SCHEMA)
            init.close()
    conn = _connect_with_retry(target, read_only)
    if not read_only:
        conn.execute(SCHEMA)
        for stmt in MIGRATIONS:
            conn.execute(stmt)
        _migrate_hacienda_log(conn)
    return conn


# ---------------------------------------------------------------- series_meta


def upsert_series_meta(conn: duckdb.DuckDBPyConnection, specs: Iterable[SeriesSpec]) -> int:
    rows = [
        (s.series_id, s.source, s.source_code, s.name, s.freq, s.unit, s.tab, s.transform, s.notes)
        for s in specs
    ]
    if rows:
        conn.executemany(
            """
            INSERT INTO series_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (series_id) DO UPDATE SET
                source = excluded.source, source_code = excluded.source_code,
                name = excluded.name, freq = excluded.freq, unit = excluded.unit,
                tab = excluded.tab, transform = excluded.transform, notes = excluded.notes
            """,
            rows,
        )
    return len(rows)


# --------------------------------------------------------------- observations


def _validate_obs(df: pd.DataFrame) -> pd.DataFrame:
    missing = set(OBS_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"observations frame missing columns: {sorted(missing)}")
    out = df[OBS_COLUMNS].copy()
    if out["value"].isna().any() or out["obs_date"].isna().any():
        raise ValueError("observations contain nulls; drop missing values at the source")
    if out.duplicated(["series_id", "obs_date"]).any():
        raise ValueError("observations contain duplicate (series_id, obs_date) rows")
    out["obs_date"] = pd.to_datetime(out["obs_date"]).dt.date
    out["value"] = out["value"].astype(float)
    return out


def upsert_observations(
    conn: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    source: str,
    fetched_at: datetime | None = None,
) -> int:
    """Upsert tidy ``[series_id, obs_date, value]`` rows.

    Rows that are new or whose value changed are also appended to ``observations_history``
    (data vintages). Returns the number of new or changed rows.
    """
    obs = _validate_obs(df)
    if obs.empty:
        return 0
    obs["source"] = source
    obs["fetched_at"] = fetched_at or utcnow()
    conn.register("_incoming", obs)
    try:
        conn.execute("BEGIN")
        changed = _first(
            conn,
            """
            INSERT INTO observations_history
            SELECT i.series_id, i.obs_date, i.value, i.source, i.fetched_at
            FROM _incoming i
            LEFT JOIN observations o USING (series_id, obs_date)
            WHERE o.value IS DISTINCT FROM i.value
            """,
        )
        conn.execute(
            """
            INSERT INTO observations
            SELECT series_id, obs_date, value, source, fetched_at FROM _incoming
            ON CONFLICT (series_id, obs_date) DO UPDATE SET
                value = excluded.value, source = excluded.source,
                fetched_at = excluded.fetched_at
            """
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.unregister("_incoming")
    return int(changed)


def read_series(
    conn: duckdb.DuckDBPyConnection, series_id: str, start: date | None = None
) -> pd.DataFrame:
    query = "SELECT obs_date, value, fetched_at FROM observations WHERE series_id = ?"
    params: list[Any] = [series_id]
    if start is not None:
        query += " AND obs_date >= ?"
        params.append(start)
    return conn.execute(query + " ORDER BY obs_date", params).df()


def read_wide(
    conn: duckdb.DuckDBPyConnection, series_ids: list[str], start: date | None = None
) -> pd.DataFrame:
    """Observations for several series, pivoted to obs_date x series_id (no filling)."""
    if not series_ids:
        return pd.DataFrame()
    query = "SELECT series_id, obs_date, value FROM observations WHERE series_id IN ?"
    params: list[Any] = [series_ids]
    if start is not None:
        query += " AND obs_date >= ?"
        params.append(start)
    long = conn.execute(query, params).df()
    if long.empty:
        return pd.DataFrame(
            columns=series_ids, index=pd.DatetimeIndex([], name="obs_date"), dtype=float
        )
    wide = long.pivot(index="obs_date", columns="series_id", values="value").sort_index()
    wide.index = pd.to_datetime(wide.index)
    return wide.reindex(columns=series_ids)


def last_fetched(conn: duckdb.DuckDBPyConnection, series_ids: list[str]) -> datetime | None:
    return _first(
        conn, "SELECT max(fetched_at) FROM observations WHERE series_id IN ?", [series_ids]
    )


def last_obs_date(conn: duckdb.DuckDBPyConnection, series_id: str) -> date | None:
    return _first(conn, "SELECT max(obs_date) FROM observations WHERE series_id = ?", [series_id])


def delete_invalid_observations(
    conn: duckdb.DuckDBPyConnection, before: date = date(1900, 1, 1)
) -> int:
    """Delete placeholder rows dated before `before` from observations (history is kept).

    Returns the number of rows deleted.
    """
    n = _first(conn, "SELECT count(*) FROM observations WHERE obs_date < ?", [before])
    if n:
        conn.execute("DELETE FROM observations WHERE obs_date < ?", [before])
    return int(n)


def read_vintages(conn: duckdb.DuckDBPyConnection, series_id: str) -> pd.DataFrame:
    return conn.execute(
        "SELECT obs_date, value, fetched_at FROM observations_history "
        "WHERE series_id = ? ORDER BY obs_date, fetched_at",
        [series_id],
    ).df()


# ------------------------------------------------------------------ documents


def upsert_document(conn: duckdb.DuckDBPyConnection, **row: Any) -> None:
    cols = [
        "doc_id",
        "kind",
        "institution",
        "meeting_id",
        "published_at",
        "title",
        "url",
        "raw_path",
        "text",
        "sha256",
    ]
    conn.execute(
        f"INSERT INTO documents ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        "ON CONFLICT (doc_id) DO UPDATE SET title = excluded.title, url = excluded.url, "
        "meeting_id = excluded.meeting_id, "
        "published_at = excluded.published_at, raw_path = excluded.raw_path, "
        "text = excluded.text",
        [row.get(c) for c in cols],
    )


def document_urls(conn: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        r[0] for r in conn.execute("SELECT url FROM documents WHERE url IS NOT NULL").fetchall()
    }


def document_shas(conn: duckdb.DuckDBPyConnection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT sha256 FROM documents").fetchall()}


def read_documents(conn: duckdb.DuckDBPyConnection, kind: str | None = None) -> pd.DataFrame:
    q = "SELECT doc_id, kind, meeting_id, published_at, title, url, sha256, text FROM documents"
    params: list[Any] = []
    if kind:
        q += " WHERE kind = ?"
        params.append(kind)
    return conn.execute(q + " ORDER BY published_at NULLS LAST", params).df()


def upsert_survey_distribution(
    conn: duckdb.DuckDBPyConnection, df: pd.DataFrame, doc_sha256: str | None
) -> int:
    if df.empty:
        return 0
    d = df.copy()
    d["doc_sha256"] = doc_sha256
    conn.register("_sd", d)
    try:
        conn.execute(
            "INSERT INTO survey_distribution SELECT survey, survey_month, section_no, section, "
            "horizon, median, p10, p90, n, doc_sha256 FROM _sd ON CONFLICT DO UPDATE SET "
            "median = excluded.median, p10 = excluded.p10, p90 = excluded.p90, n = excluded.n, "
            "doc_sha256 = excluded.doc_sha256"
        )
    finally:
        conn.unregister("_sd")
    return len(d)


def read_survey_distribution(conn: duckdb.DuckDBPyConnection, survey: str = "EEE") -> pd.DataFrame:
    return conn.execute(
        "SELECT * FROM survey_distribution WHERE survey = ? ORDER BY survey_month, section_no",
        [survey],
    ).df()


def upsert_releases(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    d = df.copy()
    d["fetched_at"] = utcnow()
    conn.register("_rel", d)
    try:
        conn.execute(
            "INSERT INTO releases SELECT indicator, title, reference_period, release_local, "
            "source, source_url, fetched_at FROM _rel ON CONFLICT DO UPDATE SET "
            "indicator = excluded.indicator, reference_period = excluded.reference_period, "
            "fetched_at = excluded.fetched_at"
        )
    finally:
        conn.unregister("_rel")
    return len(d)


def read_releases(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return conn.execute("SELECT * FROM releases ORDER BY release_local").df()


def replace_release_surprises(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Rewrite the release_surprises table (it's fully derived from observations)."""
    conn.execute("DELETE FROM release_surprises")
    if df.empty:
        return 0
    d = df.copy()
    d["computed_at"] = utcnow()
    cols = [
        "indicator",
        "reference_period",
        "release_date",
        "consensus",
        "consensus_source",
        "consensus_date",
        "actual",
        "actual_is_first_print",
        "surprise",
        "zscore",
        "revised",
        "computed_at",
    ]
    conn.register("_rs", d[cols])
    try:
        conn.execute("INSERT INTO release_surprises SELECT * FROM _rs")
    finally:
        conn.unregister("_rs")
    return len(d)


def read_release_surprises(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return conn.execute("SELECT * FROM release_surprises ORDER BY reference_period").df()


# ----------------------------------------------------------------- curve fits

CURVE_FIT_COLUMNS = ["curve", "obs_date", "params_json", "rmse", "n_points", "note"]


def upsert_curve_fits(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Upsert rows ``[curve, obs_date, params_json, rmse, n_points, note]``."""
    if df.empty:
        return 0
    d = df[CURVE_FIT_COLUMNS].copy()
    d["obs_date"] = pd.to_datetime(d["obs_date"]).dt.date
    d["computed_at"] = utcnow()
    conn.register("_cf", d)
    try:
        conn.execute(
            """
            INSERT INTO curve_fits SELECT * FROM _cf
            ON CONFLICT (curve, obs_date) DO UPDATE SET
                params_json = excluded.params_json, rmse = excluded.rmse,
                n_points = excluded.n_points, note = excluded.note,
                computed_at = excluded.computed_at
            """
        )
    finally:
        conn.unregister("_cf")
    return len(d)


def read_curve_fits(
    conn: duckdb.DuckDBPyConnection, curve: str | None = None, start: date | None = None
) -> pd.DataFrame:
    query, params = "SELECT * FROM curve_fits WHERE 1=1", []
    if curve is not None:
        query += " AND curve = ?"
        params.append(curve)
    if start is not None:
        query += " AND obs_date >= ?"
        params.append(start)
    df = conn.execute(query + " ORDER BY curve, obs_date", params).df()
    if not df.empty:
        df["obs_date"] = pd.to_datetime(df["obs_date"])
    return df


def last_curve_fit_date(conn: duckdb.DuckDBPyConnection, curve: str) -> date | None:
    d = _first(conn, "SELECT max(obs_date) FROM curve_fits WHERE curve = ?", [curve])
    return d.date() if isinstance(d, datetime) else d


def read_bond_quotes(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return conn.execute("SELECT * FROM bond_quotes ORDER BY obs_date, nemo").df()


# ------------------------------------------------------------------ snapshots


def insert_market_snapshot(
    conn: duckdb.DuckDBPyConnection,
    source: str,
    ticker: str,
    payload: dict[str, Any],
    snapshot_ts: datetime | None = None,
) -> None:
    conn.execute(
        "INSERT INTO market_snapshots VALUES (?, ?, ?, ?)",
        [source, ticker, snapshot_ts or utcnow(), json.dumps(payload)],
    )


def read_snapshots(
    conn: duckdb.DuckDBPyConnection, source: str, since: datetime | None = None
) -> pd.DataFrame:
    query = "SELECT ticker, snapshot_ts, payload_json FROM market_snapshots WHERE source = ?"
    params: list[Any] = [source]
    if since is not None:
        query += " AND snapshot_ts >= ?"
        params.append(since)
    return conn.execute(query + " ORDER BY snapshot_ts", params).df()


# ----------------------------------------------------------------- ingest_log


def log_ingest(
    conn: duckdb.DuckDBPyConnection,
    job: str,
    source: str,
    started_at: datetime,
    finished_at: datetime | None,
    status: str,
    rows: int | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO ingest_log VALUES (?, ?, ?, ?, ?, ?, ?)",
        [job, source, started_at, finished_at, status, rows, redact(error) if error else None],
    )


@dataclass
class IngestRun:
    rows: int = 0


@contextmanager
def ingest_run(conn: duckdb.DuckDBPyConnection, job: str, source: str) -> Iterator[IngestRun]:
    """Record a job in ingest_log: ``with ingest_run(conn, "job", "bcch") as run: run.rows = n``.

    Exceptions are logged with status ``error`` and re-raised.
    """
    started = utcnow()
    run = IngestRun()
    try:
        yield run
    except Exception as e:
        tb = traceback.format_exception_only(type(e), e)[-1].strip()
        log_ingest(conn, job, source, started, utcnow(), "error", run.rows, tb)
        raise
    log_ingest(conn, job, source, started, utcnow(), "ok", run.rows)


def ingest_summary(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Latest run and last success per (job, source)."""
    return conn.execute(
        """
        SELECT job, source,
               max(started_at) AS last_run,
               max(finished_at) FILTER (WHERE status = 'ok') AS last_success,
               arg_max(status, started_at) AS last_status,
               arg_max(rows, started_at) AS last_rows,
               count(*) FILTER (WHERE status = 'error') AS error_count
        FROM ingest_log GROUP BY job, source ORDER BY source, job
        """
    ).df()


def recent_errors(conn: duckdb.DuckDBPyConnection, limit: int = 20) -> pd.DataFrame:
    return conn.execute(
        "SELECT started_at, job, source, error FROM ingest_log "
        "WHERE status = 'error' ORDER BY started_at DESC LIMIT ?",
        [limit],
    ).df()


def series_status(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """series_meta joined with latest observation date and fetch time."""
    return conn.execute(
        """
        SELECT m.series_id, m.name, m.source, m.freq, m.tab,
               max(o.obs_date) AS last_obs, max(o.fetched_at) AS last_fetched,
               count(o.value) AS n_obs
        FROM series_meta m LEFT JOIN observations o USING (series_id)
        GROUP BY ALL ORDER BY m.tab, m.series_id
        """
    ).df()


# ------------------------------------------------------------------- raw files


def save_raw(
    source: str, filename: str, content: bytes, raw_dir: Path = RAW_DIR
) -> tuple[Path, str]:
    """Save a raw download under ``data/raw/<source>/<YYYY-MM-DD>/``; return (path, sha256)."""
    folder = raw_dir / source / utcnow().date().isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    path.write_bytes(content)
    return path, hashlib.sha256(content).hexdigest()


# ------------------------------------------------------------- Treasury auctions

RESULT_FIELDS = [
    "bond",
    "instrument_type",
    "maturity",
    "unit",
    "cupo",
    "demand",
    "allocated",
    "banks",
    "afp_ins_brokers",
    "rate",
    "bid_to_cover",
    "status",
    "note",
]
LIVE_SOURCES = ("live",)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is None and b is None
        return abs(float(a) - float(b)) < 1e-9
    if isinstance(a, datetime):
        a = a.date()
    if isinstance(b, datetime):
        b = b.date()
    return a == b


def upsert_auction_calendar(
    conn: duckdb.DuckDBPyConnection, rows: list[dict], today: date, now: datetime | None = None
) -> list[dict]:
    """Upsert calendar rows; a future auction that vanished from the scraped window is marked
    removed. Every change is logged in auction_calendar_changes and returned."""
    now = now or utcnow()
    changes: list[dict] = []
    seen = {(r["auction_date"], r["instrument"]) for r in rows}
    for r in rows:
        old = conn.execute(
            "SELECT amount, status FROM auction_calendar WHERE auction_date = ? AND instrument = ?",
            [r["auction_date"], r["instrument"]],
        ).fetchall()
        if not old:
            conn.execute(
                "INSERT INTO auction_calendar VALUES (?,?,?,?,?,?,?,?,?, 'active', ?, ?)",
                [
                    r["auction_date"],
                    r["instrument"],
                    r["instrument_type"],
                    r["nemo"],
                    r["isin"],
                    r["maturity"],
                    r["unit"],
                    r["amount"],
                    r["reopening"],
                    now,
                    now,
                ],
            )
            changes.append({**_chg(r, "new"), "old": None, "new": r["amount"]})
            continue
        old_amount, old_status = old[0]
        if old_amount != r["amount"] or old_status != "active":
            kind = "amount" if old_status == "active" else "reinstated"
            changes.append({**_chg(r, kind), "old": old_amount, "new": r["amount"]})
        conn.execute(
            """
            UPDATE auction_calendar SET amount = ?, instrument_type = ?, nemo = COALESCE(?, nemo),
                isin = COALESCE(?, isin), maturity = COALESCE(?, maturity), unit = ?,
                reopening = ?, status = 'active', last_seen = ?
            WHERE auction_date = ? AND instrument = ?
            """,
            [
                r["amount"],
                r["instrument_type"],
                r["nemo"],
                r["isin"],
                r["maturity"],
                r["unit"],
                r["reopening"],
                now,
                r["auction_date"],
                r["instrument"],
            ],
        )
    if rows:
        lo = min(r["auction_date"] for r in rows)
        hi = max(r["auction_date"] for r in rows)
        stale = conn.execute(
            """
            SELECT auction_date, instrument, amount FROM auction_calendar
            WHERE status = 'active' AND auction_date BETWEEN ? AND ? AND auction_date >= ?
            """,
            [lo, hi, today],
        ).fetchall()
        for d, ins, amt in stale:
            if (d, ins) not in seen:
                conn.execute(
                    "UPDATE auction_calendar SET status = 'removed', last_seen = ? "
                    "WHERE auction_date = ? AND instrument = ?",
                    [now, d, ins],
                )
                changes.append(
                    {
                        "auction_date": d,
                        "instrument": ins,
                        "change": "removed",
                        "old": amt,
                        "new": None,
                    }
                )
    for c in changes:
        conn.execute(
            "INSERT INTO auction_calendar_changes VALUES (?,?,?,?,?,?)",
            [now, c["auction_date"], c["instrument"], c["change"], c["old"], c["new"]],
        )
    return changes


def _chg(r: dict, kind: str) -> dict:
    return {"auction_date": r["auction_date"], "instrument": r["instrument"], "change": kind}


def upsert_auction_results(
    conn: duckdb.DuckDBPyConnection, rows: list[dict], source: str, now: datetime | None = None
) -> tuple[int, int, int]:
    """Insert new results; update changed ones and log each changed field as a revision.

    Live-page rows take priority: a consolidated row never overwrites a live one.
    Returns (new, revised, skipped).
    """
    now = now or utcnow()
    new = revised = skipped = 0
    cols = ", ".join(RESULT_FIELDS)
    for r in rows:
        old = conn.execute(
            f"SELECT {cols}, source FROM auction_results WHERE auction_date = ? AND nemo = ?",
            [r["auction_date"], r["nemo"]],
        ).fetchall()
        if not old:
            conn.execute(
                f"INSERT INTO auction_results (auction_date, nemo, {cols}, source, scraped_at) "
                f"VALUES (?, ?, {', '.join('?' for _ in RESULT_FIELDS)}, ?, ?)",
                [r["auction_date"], r["nemo"], *[r.get(f) for f in RESULT_FIELDS], source, now],
            )
            new += 1
            continue
        *old_vals, old_source = old[0]
        if old_source in LIVE_SOURCES and source not in LIVE_SOURCES:
            skipped += 1
            continue
        diffs = [
            (f, o, r.get(f))
            for f, o in zip(RESULT_FIELDS, old_vals, strict=True)
            if not _same(o, r.get(f))
        ]
        if not diffs and old_source == source:
            continue
        if diffs:
            revised += 1
            for f, o, n in diffs:
                conn.execute(
                    "INSERT INTO auction_results_revisions VALUES (?,?,?,?,?,?,?)",
                    [now, r["auction_date"], r["nemo"], f, _txt(o), _txt(n), source],
                )
        sets = ", ".join(f"{f} = ?" for f in RESULT_FIELDS)
        conn.execute(
            f"UPDATE auction_results SET {sets}, source = ?, scraped_at = ? "
            "WHERE auction_date = ? AND nemo = ?",
            [*[r.get(f) for f in RESULT_FIELDS], source, now, r["auction_date"], r["nemo"]],
        )
    return new, revised, skipped


def _txt(v: Any) -> str | None:
    return None if v is None else str(v)


def auction_missing_result_dates(conn: duckdb.DuckDBPyConnection, today: date) -> list[date]:
    """Active calendar auctions dated on/before today with no stored result."""
    rows = conn.execute(
        """
        SELECT DISTINCT c.auction_date FROM auction_calendar c
        LEFT JOIN auction_results r ON r.auction_date = c.auction_date
        WHERE c.status = 'active' AND c.auction_date <= ? AND r.auction_date IS NULL
        ORDER BY 1
        """,
        [today],
    ).fetchall()
    return [r[0] if isinstance(r[0], date) else r[0].date() for r in rows]


def count_calendar_on(conn: duckdb.DuckDBPyConnection, d: date) -> int:
    return int(
        _first(
            conn,
            "SELECT count(*) FROM auction_calendar WHERE status = 'active' AND auction_date = ?",
            [d],
        )
    )


def count_results_on(conn: duckdb.DuckDBPyConnection, d: date) -> int:
    return int(_first(conn, "SELECT count(*) FROM auction_results WHERE auction_date = ?", [d]))


def read_auction_results(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    df = conn.execute(
        """
        SELECT r.*, (SELECT count(*) FROM auction_results_revisions v
                     WHERE v.auction_date = r.auction_date AND v.nemo = r.nemo) > 0 AS revised
        FROM auction_results r ORDER BY auction_date DESC, nemo
        """
    ).df()
    for c in ("auction_date", "maturity"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c])
    return df


def read_auction_calendar(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    df = conn.execute("SELECT * FROM auction_calendar ORDER BY auction_date, instrument").df()
    for c in ("auction_date", "maturity"):
        df[c] = pd.to_datetime(df[c])
    return df


def read_calendar_changes(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return conn.execute(
        "SELECT * FROM auction_calendar_changes ORDER BY detected_at DESC, auction_date"
    ).df()


BOND_MASTER_COLUMNS = [
    "nemo",
    "isin",
    "instrument_type",
    "currency",
    "coupon_pct",
    "coupon_freq",
    "coupon_months",
    "issue_date",
    "maturity_date",
    "amortization",
    "day_count",
    "esg",
    "source",
    "flagged",
]


def replace_bond_master(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Rewrite bond_master (fully derived from the latest Hacienda files)."""
    d = df[BOND_MASTER_COLUMNS].copy()
    d["updated_at"] = utcnow()
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM bond_master")
        conn.register("_bm", d)
        conn.execute("INSERT INTO bond_master SELECT * FROM _bm")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.unregister("_bm")
    return len(d)


def upsert_bond_outstanding(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    conn.register("_bo", df[["nemo", "as_of", "amount", "unit"]])
    try:
        conn.execute(
            """
            INSERT INTO bond_outstanding SELECT * FROM _bo
            ON CONFLICT (nemo, as_of) DO UPDATE SET amount = excluded.amount, unit = excluded.unit
            """
        )
    finally:
        conn.unregister("_bo")
    return len(df)


def read_bond_master(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    df = conn.execute("SELECT * FROM bond_master ORDER BY instrument_type, maturity_date").df()
    for c in ("issue_date", "maturity_date"):
        df[c] = pd.to_datetime(df[c])
    return df


def read_bond_outstanding(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Latest outstanding amount per bond."""
    return conn.execute(
        """
        SELECT nemo, as_of, amount, unit FROM bond_outstanding
        QUALIFY row_number() OVER (PARTITION BY nemo ORDER BY as_of DESC) = 1
        """
    ).df()
