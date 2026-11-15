"""DuckDB storage: the only module that touches the database.

Timestamps are stored as naive TIMESTAMP values in **UTC** (columns ending in ``_at``/``_ts``).
Convert to America/New_York or America/Santiago only at display time.
"""

from __future__ import annotations

import hashlib
import json
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
CREATE TABLE IF NOT EXISTS auctions (
    auction_id     VARCHAR PRIMARY KEY,
    auction_date   DATE NOT NULL,
    issuer         VARCHAR,
    instrument     VARCHAR,
    nemotecnico    VARCHAR,
    currency       VARCHAR,
    amount_offered DOUBLE,
    amount_awarded DOUBLE,
    bids_received  DOUBLE,
    bid_to_cover   DOUBLE,
    yield_awarded  DOUBLE,
    status         VARCHAR,
    source         VARCHAR,
    fetched_at     TIMESTAMP,
    details_json   JSON
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


def connect(path: Path | str | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the database (``:memory:`` allowed) and ensure the schema exists."""
    target = str(path) if path is not None else str(DEFAULT_DB_PATH)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        if read_only and not Path(target).exists():
            init = duckdb.connect(target)
            init.execute(SCHEMA)
            init.close()
    conn = duckdb.connect(target, read_only=read_only)
    if not read_only:
        conn.execute(SCHEMA)
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
        return pd.DataFrame(columns=series_ids)
    wide = long.pivot(index="obs_date", columns="series_id", values="value").sort_index()
    wide.index = pd.to_datetime(wide.index)
    return wide.reindex(columns=series_ids)


def last_fetched(conn: duckdb.DuckDBPyConnection, series_ids: list[str]) -> datetime | None:
    return _first(
        conn, "SELECT max(fetched_at) FROM observations WHERE series_id IN ?", [series_ids]
    )


def last_obs_date(conn: duckdb.DuckDBPyConnection, series_id: str) -> date | None:
    return _first(conn, "SELECT max(obs_date) FROM observations WHERE series_id = ?", [series_id])


def read_vintages(conn: duckdb.DuckDBPyConnection, series_id: str) -> pd.DataFrame:
    return conn.execute(
        "SELECT obs_date, value, fetched_at FROM observations_history "
        "WHERE series_id = ? ORDER BY obs_date, fetched_at",
        [series_id],
    ).df()


# ------------------------------------------------------------------ documents


def upsert_document(conn: duckdb.DuckDBPyConnection, **row: Any) -> None:
    cols = ["doc_id", "kind", "institution", "meeting_id", "published_at", "title", "url",
            "raw_path", "text", "sha256"]  # fmt: skip
    conn.execute(
        f"INSERT INTO documents ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        "ON CONFLICT (doc_id) DO UPDATE SET title = excluded.title, url = excluded.url, "
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
