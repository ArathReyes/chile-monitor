"""Backtest the market-implied TPM path and write docs/backtests/01-implied-path.{md,csv}.

Usage: uv run python scripts/backtest_implied_path.py [--db data/monitor_backtest.duckdb]
Reads a copy of the DuckDB (read-only); never fetches data.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from monitor import storage
from monitor.config import load_meetings
from monitor.models.backtest import (
    DEAD_BAND_BP,
    EOF_LAG_DAYS,
    QUOTE_MAX_AGE_DAYS,
    Meeting,
    metrics,
    run_backtest,
    sensitivity_grid,
    summarize,
)
from monitor.models.surprises import EEE_PUBLICATION_LAG_DAYS, EOF_MAX_AGE_DAYS
from monitor.models.tpm_implied import FitConfig

SERIES = [
    "cl_tpm", "cl_tib", "cl_spc_nom_90d", "cl_spc_nom_180d", "cl_spc_nom_360d",
    "eof_tpm_next", "eof_tpm_rpm1", "eof_tpm_rpm2_3m", "eof_tpm_rpm3", "eof_tpm_rpm4_6m",
    "eof_tpm_12m", "eof_tpm_24m", "eee_tpm_this_month", "eee_tpm_next_month",
]  # fmt: skip
OUT_DIR = Path("docs/backtests")
METHOD_NAMES = {"model": "Implied path", "eof": "EOF survey", "rw": "Random walk (0)",
                "spc90": "SPC-90d single step"}  # fmt: skip
GRID_MUS = (0.0, 0.01, 0.05, 0.25, 1.0)  # spec grid {0.05, 0.25, 1} plus 0 and 0.01
OLD_MU = 0.25  # default before 2026-11-15
CONFIGS = ((0.005, 0.25), (0.005, 0.05), (0.02, 0.05), (0.005, 0.01))
TARGET_NAMES = {"m1": "Meeting 1 change", "cum2": "Cumulative to meeting 2",
                "cum3": "Cumulative to meeting 3"}  # fmt: skip


# Written from the first run of 2026-11-15 (μ = 0.25, data through 2026-11-13); the
# recommendation was applied the same day. Revisit if the numbers move.
CONCLUSIONS = """\
Written from the first run (μ = 0.25); the recommendation at the end has been applied (see
'Changes applied' at the top). Tables above are from the current defaults.

1. **Meeting 1, short horizons.** At h = 1-5 bd the implied path cuts the random-walk error by
   ~75% and beats the SPC-90d single-step approximation clearly (MAE ~10 vs ~15-18bp), which
   systematically overshoots because it loads every move inside the 90-day window onto one
   meeting. The EOF survey has the lowest MAE at h = 1 (it lands exactly on the 25bp grid when
   right) but a higher RMSE than the model: when the survey misses (hiking cycle) it misses big.
2. **h = 20 bd.** All methods degrade; the model and SPC-90d are close to the survey, and the
   bucket hit rate drops below 50%. Read the path 4 weeks out as direction, not size.
3. **Regimes.** Most error comes from the 2021-22 hiking cycle (outsized hikes beyond survey and
   market pricing). In the cutting cycle and 2026 the model is within ~3-7bp at h = 1. In the
   2022-23 plateau the curve priced cuts early (inverted SPC), so the model shows -10 to -30bp at
   holds; the survey (0) was right there.
4. **Smoothness is too stiff.** With μ = 0.25 the fit RMSE on the three swaps is 10-25bp in
   2021-23 (vs ~4bp with μ = 0.05): the penalty on a 100bp step (2,500) dwarfs a 14bp swap miss
   (~200 per swap), so large moves get spread over neighbouring meetings. That is the source of
   the negative bias in the hiking cycle and of the early-cut miss in Jun-2023.
5. **Cumulative to meetings 2-3** is hard for everyone (MAE ~57-62bp at h = 1, n = 43); the model
   is on par with the EOF and far better than a random walk. λ barely matters (3 swaps pin the
   first meetings; λ mainly shapes the tail).
6. **Prior alignment bug** (the app mapped EOF 'next' to the first future step even when that
   meeting had been held since the round; fixed 2026-11-15): affects ~20% of backtest rows but
   changes MAE by < 0.2bp at λ = 0.005, so it was cosmetic; it matters if λ is raised.

**Recommendation:** μ = 0.05 (from 0.25), keep λ = 0.005 (0.02 is equivalent within noise).
Meeting-1 MAE falls from 10.0 / 11.2 / 18.1bp to 8.8 / 9.3 / 14.7bp at h = 1 / 5 / 20, bias
goes from -4.5 / -6.4 / -12.1bp to about +1 / -1 / -5bp, and the 25bp-bucket hit rate rises from
71% to 80% at h = 1. Cumulative-to-meeting-3 error is unchanged. μ ≤ 0.01 adds noise at h = 1-5.
"""


def load_series(db: str) -> dict[str, pd.Series]:
    conn = storage.connect(db, read_only=True)
    try:
        out = {}
        for sid in SERIES:
            df = storage.read_series(conn, sid)
            out[sid] = pd.Series(df["value"].to_numpy(), index=pd.to_datetime(df["obs_date"]))
        return out
    finally:
        conn.close()


def load_calendar() -> tuple[list[Meeting], int]:
    """Confirmed meetings plus future unconfirmed ones (steps beyond the last confirmed meeting)."""
    ms = load_meetings().bcch.meetings
    confirmed = [m for m in ms if m.confirmed]
    last = max(m.decision_date for m in confirmed)
    future = [m for m in ms if not m.confirmed and m.decision_date > last]
    tuples = [(m.decision_date, m.effective, m.meeting_id) for m in [*confirmed, *future]]
    return sorted(tuples), len(confirmed)


# ------------------------------------------------------------------- markdown


def fmt(x: object, nd: int = 1) -> str:
    if isinstance(x, float):
        return "" if pd.isna(x) else f"{x:.{nd}f}"
    return str(x)


def md_table(df: pd.DataFrame, nd: int = 1) -> str:
    head = "| " + " | ".join(map(str, df.columns)) + " |"
    sep = "|" + "|".join("---" for _ in df.columns) + "|"
    body = ["| " + " | ".join(fmt(v, nd) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join([head, sep, *body])


def headline(summary: pd.DataFrame, target: str, regime: str = "all") -> pd.DataFrame:
    s = summary[(summary["target"] == target) & (summary["regime"] == regime)].copy()
    s["method"] = s["method"].map(METHOD_NAMES)
    s["dir_hit"] = s["dir_hit"] * 100
    s["bucket_hit"] = s["bucket_hit"] * 100
    s = s.sort_values(["horizon_bd", "method"])
    cols = ["horizon_bd", "method", "n", "mae_bp", "rmse_bp", "bias_bp", "dir_hit", "bucket_hit"]
    return s[cols].rename(columns={"horizon_bd": "h (bd)", "dir_hit": "dir hit %",
                                   "bucket_hit": "25bp bucket %"})  # fmt: skip


def regime_table(summary: pd.DataFrame, target: str = "m1") -> pd.DataFrame:
    s = summary[(summary["target"] == target) & (summary["regime"] != "all")]
    s = s[s["method"].isin(["model", "eof", "rw", "spc90"])]
    p = s.pivot_table(index=["regime", "horizon_bd"], columns="method", values="mae_bp")
    n = s[s["method"] == "model"].set_index(["regime", "horizon_bd"])["n"]
    p.insert(0, "n", n)
    order = {"hiking": 0, "plateau": 1, "cutting": 2, "2026": 3}
    p = p.reset_index().sort_values(["regime", "horizon_bd"], key=lambda c: c.map(order).fillna(c))
    return p.rename(columns={"horizon_bd": "h (bd)", **METHOD_NAMES})


def worst_misses(df: pd.DataFrame, k: int = 8) -> pd.DataFrame:
    d = df[df["horizon_bd"] == 1].dropna(subset=["model_m1_bp"]).copy()
    d["err_bp"] = d["model_m1_bp"] - d["actual_change_bp"]
    d = d.reindex(d["err_bp"].abs().sort_values(ascending=False).index).head(k)
    cols = ["decision_date", "as_of", "actual_change_bp", "model_m1_bp", "err_bp", "eof_m1_bp",
            "spc90_m1_bp", "prior_m1_bp", "spread_bp", "fit_rmse_bp"]  # fmt: skip
    return d[cols]


def skipped(df: pd.DataFrame) -> pd.DataFrame:
    s = df[df["skip_reason"] != ""]
    return s[["meeting_id", "decision_date", "horizon_bd", "as_of", "skip_reason"]]


def prior_alignment_check(df: pd.DataFrame, df_app: pd.DataFrame) -> pd.DataFrame:
    """Model accuracy on rows where the latest EOF round predates a meeting already held."""
    key = ["meeting_id", "horizon_bd"]
    aff = df[df["prior_offset"] > 0][key]
    out = []
    for name, d in (("aligned prior (current)", df), ("old app mapping (unaligned)", df_app)):
        x = d.merge(aff, on=key)
        for t in ("m1", "cum3"):
            act = "actual_change_bp" if t == "m1" else "actual_cum3_bp"
            m = metrics(x[f"model_{t}_bp"], x[act])
            out.append({"prior": name, "target": t, "n": m["n"], "mae_bp": m["mae_bp"],
                        "rmse_bp": m["rmse_bp"]})  # fmt: skip
    return pd.DataFrame(out)


def config_comparison(series: dict[str, pd.Series], meetings: list[Meeting]) -> pd.DataFrame:
    """Default vs candidate λ/μ at every horizon (meeting 1 and cumulative to meeting 3)."""
    rows = []
    for lam, mu in CONFIGS:
        df = run_backtest(series, meetings, cfg=FitConfig(lam=lam, mu=mu))
        for h, g in df.groupby("horizon_bd"):
            m1 = metrics(g["model_m1_bp"], g["actual_change_bp"])
            c3 = metrics(g["model_cum3_bp"], g["actual_cum3_bp"])
            rows.append({"lam": lam, "mu": mu, "h (bd)": h, "mae_m1": m1["mae_bp"],
                         "rmse_m1": m1["rmse_bp"], "bias_m1": m1["bias_bp"],
                         "bucket_m1 %": m1["bucket_hit"] * 100, "mae_cum3": c3["mae_bp"],
                         "rmse_cum3": c3["rmse_bp"]})  # fmt: skip
    return pd.DataFrame(rows)


def changes_table(configs: pd.DataFrame) -> pd.DataFrame:
    """Meeting-1 headline metrics, previous default μ vs current default (λ fixed)."""
    old, new = FitConfig(mu=OLD_MU), FitConfig()
    rows = []
    for h in sorted(configs["h (bd)"].unique()):
        g = configs[configs["h (bd)"] == h]
        pick = {
            name: g[(g["lam"] == c.lam) & (g["mu"] == c.mu)].iloc[0]
            for name, c in (("before", old), ("after", new))
        }
        row = {"h (bd)": h}
        for col, label in (("mae_m1", "MAE"), ("bias_m1", "bias"), ("bucket_m1 %", "bucket %")):
            row[f"{label} before"] = pick["before"][col]
            row[f"{label} after"] = pick["after"][col]
        rows.append(row)
    return pd.DataFrame(rows)


def changes_section(configs: pd.DataFrame) -> list[str]:
    return [
        "## Changes applied (2026-11-15)",
        f"- Smoothness default μ {OLD_MU} → {FitConfig().mu} (λ stays {FitConfig().lam}): "
        "`FitConfig`, the app's sliders and `implied_fit` defaults.",
        "- Prior alignment: when meetings were held between the latest EOF round and the curve "
        "date, the round's meeting positions now shift by that count (`tpm_implied.eof_prior`, "
        "shared by the app and this harness; previously the app mapped the round's 'next' to the "
        "first future meeting).",
        "- Single EOF round: the app now takes every EOF key from the latest survey date "
        "(`tpm_implied.latest_eof_round`) instead of each series' own latest value.",
        "- The harness already used the aligned, single-round prior, so the metric changes below "
        "come from μ; the prior fixes change the live app (see 'Prior alignment').",
        "Meeting-1 change, all regimes (MAE and bias in bp; before = μ "
        f"{OLD_MU}, after = μ {FitConfig().mu}):",
        md_table(changes_table(configs)),
    ]


def write_report(
    df: pd.DataFrame,
    summary: pd.DataFrame,
    grid: pd.DataFrame,
    configs: pd.DataFrame,
    align: pd.DataFrame,
    n_confirmed: int,
    conclusions: str,
) -> str:
    first, last = df["decision_date"].min(), df["decision_date"].max()
    parts = [
        "# Backtest 01: market-implied TPM path",
        f"Generated {date.today():%Y-%m-%d} by `scripts/backtest_implied_path.py` from a copy of "
        f"`data/monitor.duckdb`. Model: `monitor.models.tpm_implied.fit_path`; harness: "
        f"`monitor.models.backtest`. Per-row results: `01-implied-path.csv`.",
        *changes_section(configs),
        "## Method",
        f"- Sample: {df['meeting_id'].nunique()} of {n_confirmed} confirmed BCCh meetings "
        f"({first} to {last}, holds included). Steps use true meeting dates (the BCCh publishes "
        "its calendar in advance).",
        "- For each meeting and horizon h ∈ {1, 5, 20} CL business days, the as-of date is the "
        "decision date minus h business days. Every input is restricted to that date and the "
        "path is refit (N = 8 steps, Kalshi anchor off, 2y swap off).",
        "- Realized change at meeting 1 = TPM on the effective date − TPM on the decision date. "
        "Realized cumulative change to meetings 2 and 3 = TPM on their effective dates − TPM at "
        "the as-of date (same base as the implied cumulative change).",
        "- Benchmarks: **EOF survey** (`survey_expectation` logic, cutoff = as-of date; position-"
        "aware so a round taken before an intervening meeting uses the right key; EEE fallback "
        "for meeting 1 only), **random walk** (no change), **SPC-90d single step** "
        "(`market_implied_change` logic with the as-of quote: one move, no spread, no prior).",
        "## Information-set assumptions (no look-ahead)",
        f"- SPC 90/180/360d: latest quote dated ≤ as-of (BDE date = trade date, quote = close "
        f"of that day), tenor skipped if older than {QUOTE_MAX_AGE_DAYS} days; accrual starts "
        "T+2 CL business days (`spc_start`), 360 basis.",
        f"- EOF: a round dated d is used from d + {EOF_LAG_DAYS} day (results are published after"
        f" the survey closes). The benchmark needs a round within {EOF_MAX_AGE_DAYS} days of the "
        "decision. The prior uses all keys from the single latest round "
        "(`tpm_implied.latest_eof_round`, same as the app).",
        f"- EEE: dated the 1st of the month, used {EEE_PUBLICATION_LAG_DAYS} days later.",
        "- TPM, TIB (spread = 20-obs average TIB − TPM): observations dated ≤ as-of.",
        "- Prior mapping = `tpm_implied.eof_prior`, shared with `app/mp_data.implied_fit` (EOF "
        "next, rpm1, rpm2_3m, rpm3, rpm4_6m by meeting position; 12m → 365 days, 24m → 730 days); "
        "meeting positions shift by the number of meetings held between the EOF round and the "
        "first step (see 'Prior alignment' below).",
        f"- Direction hit: sign of change with a ±{DEAD_BAND_BP:.0f}bp dead band (inside = hold). "
        "25bp bucket hit: implied change rounded to the nearest 25bp equals the realized change.",
        "## Headline: meeting-1 change (all regimes)",
        md_table(headline(summary, "m1")),
        "## Cumulative change to meetings 2 and 3",
        "SPC-90d single step has no multi-meeting version; random walk = 0.",
        "### To meeting 2",
        md_table(headline(summary, "cum2")),
        "### To meeting 3",
        md_table(headline(summary, "cum3")),
        "## Meeting-1 MAE (bp) by regime",
        "Hiking 2021-05..2022-10, plateau 2022-11..2023-06, cutting 2023-07..2025-12, 2026.",
        md_table(regime_table(summary)),
        "## λ/μ sensitivity (h = 1 bd)",
        "λ = prior weight, μ = smoothness weight (per bp², swap weight 1). Current defaults "
        f"λ = {FitConfig().lam}, μ = {FitConfig().mu} (μ was {OLD_MU} before 2026-11-15).",
        "The spec grid is μ ∈ {0.05, 0.25, 1}; μ = 0 and 0.01 were added after the spec grid "
        "showed MAE falling as μ falls.",
        md_table(grid, 2),
        "### Candidate settings at every horizon",
        md_table(configs, 2),
        "## Worst meeting-1 misses (h = 1 bd, default λ/μ)",
        md_table(worst_misses(df)),
        "## Prior alignment",
        "Rows where the latest EOF round predates a meeting already held by the as-of date "
        f"({int((df['prior_offset'] > 0).sum())} rows). The old app mapping (before 2026-11-15) "
        "assigned the round's 'next' to the first future step, i.e. one meeting too late; app "
        "and backtest now share the aligned mapping (`tpm_implied.eof_prior`).",
        md_table(align),
        "## Skipped rows",
        md_table(skipped(df)) if len(skipped(df)) else "None.",
        "## Conclusions",
        conclusions,
    ]
    return "\n\n".join(parts) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/monitor_backtest.duckdb")
    args = ap.parse_args()
    series = load_series(args.db)
    meetings, n_confirmed = load_calendar()
    df = run_backtest(series, meetings)
    df_app = run_backtest(series, meetings, align_prior=False)
    summary = summarize(df)
    grid = sensitivity_grid(series, meetings, mus=GRID_MUS)
    configs = config_comparison(series, meetings)
    align = prior_alignment_check(df, df_app)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_DIR / "01-implied-path.csv", index=False)
    report = write_report(df, summary, grid, configs, align, n_confirmed, CONCLUSIONS)
    (OUT_DIR / "01-implied-path.md").write_text(report)
    pd.set_option("display.width", 200)
    print(headline(summary, "m1").to_string(index=False))
    print(headline(summary, "cum3").to_string(index=False))
    print(grid.to_string(index=False))
    print(configs.to_string(index=False))
    print(worst_misses(df).to_string(index=False))
    print(align.to_string(index=False))
    print(skipped(df).to_string(index=False))
    print(regime_table(summary).to_string(index=False))


if __name__ == "__main__":
    main()
