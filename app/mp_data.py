"""Data assembly for the Monetary Policy tab: reads DuckDB + runs models (no API calls,
except Refresh, which runs the jobs layer). Cached with st.cache_data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import streamlit as st

from monitor import storage
from monitor.cl_calendar import spc_start
from monitor.config import load_meetings, load_series
from monitor.health import is_stale
from monitor.models.prediction_markets import implied_probabilities
from monitor.models.surprises import (
    decisions_from_meetings,
    decisions_from_tpm,
    surprise_table,
)
from monitor.models.tpm_implied import (
    Anchor,
    FitConfig,
    PathFit,
    SwapQuote,
    average_spread_bp,
    build_prior,
    build_steps,
    fit_path,
)

TTL = 600
N_STEPS = 8
SHORT_SWAPS = [("cl_spc_nom_90d", 90), ("cl_spc_nom_180d", 180), ("cl_spc_nom_360d", 360)]
EOF_MEETING_KEYS = ["next", "rpm1", "rpm2_3m", "rpm3", "rpm4_6m"]
EOF_IDS = [f"eof_tpm_{k}" for k in [*EOF_MEETING_KEYS, "12m", "24m"]]
EEE_HORIZONS = {"this_month": 0, "next_month": 1, "5m": 5, "11m": 11, "17m": 17, "23m": 23}
EEE_IDS = [f"eee_tpm_{k}" for k in EEE_HORIZONS]


# ------------------------------------------------------------------ raw reads


@st.cache_data(ttl=TTL)
def wide(series_ids: tuple[str, ...], start: date | None = None) -> pd.DataFrame:
    conn = storage.connect()
    try:
        return storage.read_wide(conn, list(series_ids), start)
    finally:
        conn.close()


@st.cache_data(ttl=TTL)
def fetched_at(series_ids: tuple[str, ...]):
    conn = storage.connect()
    try:
        return storage.last_fetched(conn, list(series_ids))
    finally:
        conn.close()


def series(sid: str, start: date | None = None) -> pd.Series:
    return wide((sid,), start)[sid].dropna()


def asof_series(sid: str, d: date) -> pd.Series:
    s = series(sid)
    return s.loc[: pd.Timestamp(d)]


def stale(sid: str) -> tuple[bool, date | None]:
    s = series(sid)
    last = s.index[-1].date() if len(s) else None
    freq = load_series().by_id(sid).freq
    return is_stale(last, freq, date.today()), last


def meetings() -> list[tuple[date, date, str]]:
    return [(m.decision_date, m.effective, m.meeting_id) for m in load_meetings().bcch.meetings]


# ----------------------------------------------------------- prediction markets


@st.cache_data(ttl=120)
def market_outcomes() -> tuple[pd.DataFrame, pd.DataFrame, object]:
    """Latest snapshot per (venue, event) -> (outcomes with probs, summary, snapshot_ts)."""
    conn = storage.connect()
    try:
        frames, ts_max = [], None
        for venue in ("kalshi", "polymarket"):
            snap = storage.read_snapshots(conn, venue)
            if snap.empty:
                continue
            last = snap.sort_values("snapshot_ts").groupby("ticker").tail(1)
            for row in last.itertuples(index=False):
                df = pd.DataFrame(json.loads(row.payload_json)["outcomes"])
                df["snapshot_ts"] = row.snapshot_ts
                frames.append(df)
                ts_max = row.snapshot_ts if ts_max is None else max(ts_max, row.snapshot_ts)
    finally:
        conn.close()
    if not frames:
        return pd.DataFrame(), pd.DataFrame(), None
    out = pd.concat(frames, ignore_index=True)
    out["meeting_date"] = pd.to_datetime(out["meeting_date"]).dt.date
    out = out[out["meeting_date"] >= date.today()]
    if out.empty:
        return pd.DataFrame(), pd.DataFrame(), ts_max
    probs, summary = implied_probabilities(out)
    return probs, summary.sort_values("meeting_date"), ts_max


@st.cache_data(ttl=TTL)
def kalshi_history() -> pd.DataFrame:
    """Daily normalized probabilities per outcome from Kalshi candles."""
    conn = storage.connect()
    try:
        snap = storage.read_snapshots(conn, "kalshi_candles")
    finally:
        conn.close()
    if snap.empty:
        return pd.DataFrame()
    p = pd.json_normalize(snap["payload_json"].map(json.loads))
    df = pd.concat([snap[["ticker", "snapshot_ts"]].reset_index(drop=True), p], axis=1)
    df["event"] = df["ticker"].str.rsplit("-", n=1).str[0]
    df["outcome"] = df["ticker"].str.rsplit("-", n=1).str[-1]
    df["mid"] = (df["yes_bid"].fillna(0) + df["yes_ask"]) / 2
    df["date"] = pd.to_datetime(df["snapshot_ts"]).dt.normalize()
    df["prob"] = df["mid"] / df.groupby(["event", "date"])["mid"].transform("sum")
    return df


# ---------------------------------------------------------------- implied path


@dataclass
class FitInputs:
    quotes: list[SwapQuote]
    tpm0: float
    tpm_date: date
    curve_date: date
    survey_date: date | None
    eof_latest: dict[str, float]
    spread_bp: float


def fit_inputs(as_of: date | None = None, use_2y: bool = False, w2y: float = 0.1) -> FitInputs:
    as_of = as_of or date.today()
    tpm = asof_series("cl_tpm", as_of)
    quotes = []
    for sid, days in SHORT_SWAPS:
        s = asof_series(sid, as_of)
        if len(s):
            quotes.append(
                SwapQuote(
                    sid, spc_start(s.index[-1].date()), float(s.iloc[-1]), 360, tenor_days=days
                )
            )
    if use_2y:
        s = asof_series("cl_spc_nom_2y", as_of)
        if len(s):
            quotes.append(
                SwapQuote(
                    "cl_spc_nom_2y",
                    spc_start(s.index[-1].date()),
                    float(s.iloc[-1]),
                    365,
                    tenor_years=2,
                    weight=w2y,
                )
            )
    eof = {}
    survey_date = None
    for k in [*EOF_MEETING_KEYS, "12m", "24m"]:
        s = asof_series(f"eof_tpm_{k}", as_of)
        if len(s):
            eof[k] = float(s.iloc[-1])
            survey_date = max(survey_date or s.index[-1].date(), s.index[-1].date())
    tib = asof_series("cl_tib", as_of)
    return FitInputs(
        quotes=quotes,
        tpm0=float(tpm.iloc[-1]),
        tpm_date=tpm.index[-1].date(),
        curve_date=max(
            asof_series(sid, as_of).index[-1].date()
            for sid, _ in SHORT_SWAPS
            if len(asof_series(sid, as_of))
        ),
        survey_date=survey_date,
        eof_latest=eof,
        spread_bp=average_spread_bp(tib, tpm),
    )


def market_anchors(steps, venues: tuple[str, ...], tpm0: float, weight: float) -> list[Anchor]:
    _, summary, _ = market_outcomes()
    if summary.empty or not venues:
        return []
    # Anchor only the first step: the expected change there is relative to tpm0. Later
    # meetings' expected changes are conditional on earlier outcomes, so they are not anchored.
    first = steps[0]
    out = []
    for r in summary.itertuples(index=False):
        if r.venue in venues and r.quality == "ok" and r.meeting_date == first.decision_date:
            out.append(
                Anchor(first.effective_date, tpm0 + r.expected_change_bp / 100, weight, r.venue)
            )
    return out


@st.cache_data(ttl=TTL)
def implied_fit(
    lam: float = 0.005,
    mu: float = 0.25,
    anchor_venues: tuple[str, ...] = ("kalshi",),
    anchor_weight: float = 4.0,
    use_2y: bool = False,
    w2y: float = 0.1,
    as_of: date | None = None,
) -> tuple[PathFit, FitInputs, np.ndarray]:
    fi = fit_inputs(as_of, use_2y, w2y)
    steps = build_steps(meetings(), fi.curve_date, N_STEPS)
    e = fi.eof_latest
    prior = build_prior(
        steps,
        fi.tpm0,
        fi.survey_date,
        {i: e[k] for i, k in enumerate(EOF_MEETING_KEYS) if k in e},
        {d: e[k] for d, k in ((365, "12m"), (730, "24m")) if k in e},
    )
    anchors = [] if as_of else market_anchors(steps, anchor_venues, fi.tpm0, anchor_weight)
    fit = fit_path(
        fi.quotes, fi.tpm0, steps, prior, anchors, FitConfig(lam=lam, mu=mu, spread_bp=fi.spread_bp)
    )
    return fit, fi, prior


@st.cache_data(ttl=TTL)
def sensitivity_band(lam: float, mu: float, use_2y: bool, w2y: float) -> pd.DataFrame:
    """Min/max implied path across lam/mu x {1/4, 4}."""
    paths = []
    for f in (0.25, 4.0):
        fit, _, _ = implied_fit(lam * f, mu * f, ("kalshi",), 4.0, use_2y, w2y)
        paths.append(fit.path.set_index("effective_date")["implied_tpm"])
    df = pd.concat(paths, axis=1)
    return pd.DataFrame({"lo": df.min(axis=1), "hi": df.max(axis=1)})


# ------------------------------------------------------------------- decisions


def latest_available(monthly: pd.Series, d: date, lag_days: int) -> float | None:
    """Latest monthly value plausibly published by date d (obs month start + lag <= d)."""
    s = monthly.dropna()
    s = s[s.index + pd.Timedelta(days=lag_days) <= pd.Timestamp(d)]
    return float(s.iloc[-1]) if len(s) else None


@st.cache_data(ttl=TTL)
def decision_table(since: date = date(2010, 1, 1)) -> pd.DataFrame:
    tpm = series("cl_tpm")
    if tpm.empty:
        return pd.DataFrame()
    dec = decisions_from_tpm(tpm)
    mt = decisions_from_meetings(meetings(), tpm, date.today())
    if not mt.empty:
        # drop TPM-derived rows that fall within a configured meeting's window
        covered = set()
        for d in mt["decision_date"]:
            covered |= {d + pd.Timedelta(days=k) for k in range(0, 6)}
        dec = dec[~dec["effective_date"].isin(covered)]
        dec = pd.concat([dec, mt], ignore_index=True)
    dec = dec[dec["decision_date"] >= since].sort_values("decision_date").reset_index(drop=True)
    tab = surprise_table(
        dec,
        series("eof_tpm_next"),
        series("eee_tpm_this_month"),
        series("eee_tpm_next_month"),
        series("cl_spc_nom_90d"),
    )
    from monitor.models.surprises import yoy

    cpi_yoy = series("cl_cpi_yoy")
    core_yoy = yoy(series("cl_cpi_core_spliced"))
    imacec_yoy = yoy(series("cl_imacec"))
    tab["cpi_yoy"] = [latest_available(cpi_yoy, d, 40) for d in tab["decision_date"]]
    tab["core_yoy"] = [latest_available(core_yoy, d, 40) for d in tab["decision_date"]]
    tab["imacec_yoy"] = [latest_available(imacec_yoy, d, 62) for d in tab["decision_date"]]
    votes = vote_labels()
    tab["vote"] = [votes.get(d, "") for d in tab["decision_date"]]
    tones = statement_tones().set_index("meeting_date")["tone"].to_dict()
    tab["statement_tone"] = [tones.get(d) for d in tab["decision_date"]]
    return tab


def next_meeting() -> tuple[date | None, str]:
    future = [m for m in meetings() if m[0] >= date.today()]
    if future:
        m = min(future)
        return m[0], m[2]
    return None, ""


def days_until(d: date | None) -> int | None:
    return (d - date.today()).days if d else None


# --------------------------------------------------------------------- refresh

MP_SERIES = [
    "cl_tpm",
    "cl_tib",
    "cl_spc_nom_90d",
    "cl_spc_nom_180d",
    "cl_spc_nom_360d",
    "cl_spc_nom_2y",
    *EOF_IDS,
    *EEE_IDS,
    "eee_tpm_dec2026",
    "eee_tpm_dec2027",
    "cl_cpi_yoy",
    "cl_cpi_core_spliced",
    "cl_imacec",
    "eof_statement_bias",
]


def refresh(markets: bool = True, bde: bool = True) -> dict[str, object]:
    """Run the ingestion jobs this tab needs, then drop cached data."""
    from monitor.jobs import bcch_series, snapshot_markets

    out: dict[str, object] = {}
    if bde:
        out["bde"] = bcch_series.run(series_ids=MP_SERIES)
    if markets:
        out["markets"] = snapshot_markets.run(candles=True)
    st.cache_data.clear()
    return out


@st.cache_data(ttl=TTL)
def eee_distribution() -> pd.DataFrame:
    """TPM rows from parsed EEE results PDFs (empty until PDFs are saved manually)."""
    conn = storage.connect()
    try:
        df = storage.read_survey_distribution(conn, "EEE")
    finally:
        conn.close()
    if df.empty:
        return df
    return df[df["section"].str.contains("Pol[ií]tica", regex=True)].reset_index(drop=True)


# ------------------------------------------------------------------ statements


@st.cache_data(ttl=TTL)
def statements() -> pd.DataFrame:
    """Parsed press releases: one row per meeting with text and parsed decision/vote."""
    from monitor.models.statements import parse_statement

    conn = storage.connect()
    try:
        docs = storage.read_documents(conn, "statements")
    finally:
        conn.close()
    rows = []
    for r in docs.itertuples(index=False):
        if not isinstance(r.text, str):
            continue
        try:
            p = parse_statement(r.text)
        except ValueError:
            continue
        rows.append(
            {
                "meeting_date": p.meeting_date,
                "meeting_id": p.meeting_id,
                "parsed": p,
                "text": r.text,
                "doc_id": r.doc_id,
                "sha256": r.sha256,
            }
        )
    df = pd.DataFrame(rows)
    return df.sort_values("meeting_date").reset_index(drop=True) if not df.empty else df


def parsed_statements() -> list:
    s = statements()
    return [] if s.empty else list(s["parsed"])


def vote_labels() -> dict[date, str]:
    from monitor.models.board import vote_record

    rec = vote_record(parsed_statements())
    return {
        r.meeting_date: r.split if r.unanimous else f"{r.split} ({r.dissent_summary})"
        for r in rec.itertuples(index=False)
    }


@st.cache_data(ttl=TTL)
def survey_vs_market(since: date = date(2021, 5, 1)) -> pd.DataFrame:
    """EOF next-RPM expected change vs SPC-90d-implied change on each EOF round date.

    Target meeting = first configured meeting after the survey date (needs the full calendar).
    """
    from monitor.models.surprises import market_implied_change

    eof = series("eof_tpm_next")
    tpm = series("cl_tpm")
    spc = series("cl_spc_nom_90d")
    ms = sorted(meetings())
    rows = []
    for ts, exp in eof[eof.index >= pd.Timestamp(since)].items():
        d = ts.date()
        nxt = next((m for m in ms if m[0] > d), None)
        if nxt is None or (nxt[0] - d).days > 70:
            continue
        before = tpm.loc[:ts]
        if before.empty:
            continue
        t0 = float(before.iloc[-1])
        mkt = market_implied_change(nxt[0], nxt[1], t0, spc.loc[:ts], max_quote_age_days=75)
        rows.append(
            {
                "survey_date": d,
                "target_meeting": nxt[0],
                "tpm": t0,
                "eof_change_bp": (exp - t0) * 100,
                "spc_change_bp": mkt,
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df["gap_bp"] = df["spc_change_bp"] - df["eof_change_bp"]
    return df


# ----------------------------------------------------------------------- board


@st.cache_data(ttl=TTL)
def board_data() -> dict[str, object]:
    """Authorities list, profiles, photos and overrides from data/manual/bcch_web/board/."""
    import yaml

    from monitor.config import CONFIG_DIR
    from monitor.sources import board as board_src

    try:
        auth = board_src.load_authorities()
    except ValueError as e:
        auth, err = pd.DataFrame(), str(e)
    else:
        err = None
    profiles = board_src.load_profiles()
    ov_path = CONFIG_DIR / "board_overrides.yaml"
    overrides = yaml.safe_load(ov_path.read_text(encoding="utf-8")) if ov_path.exists() else {}
    current = auth[auth["end_year"].isna()] if not auth.empty else auth
    photos = {
        r.surname_key: str(p)
        for r in current.itertuples(index=False)
        if (p := board_src.find_photo(r.full_name))
    }
    return {
        "authorities": auth,
        "profiles": profiles,
        "photos": photos,
        "aliases": (overrides or {}).get("aliases") or {},
        "error": err,
    }


@st.cache_data(ttl=TTL)
def statement_tones() -> pd.DataFrame:
    """EOF tone (0 dovish, 5 neutral, 10 hawkish) of each meeting's statement."""
    from monitor.models.board import statement_tone

    past = [m[0] for m in meetings() if m[0] <= date.today()]
    return statement_tone(past, series("eof_statement_bias"))
