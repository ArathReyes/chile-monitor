"""Implied probabilities from prediction-market outcome quotes (pure)."""

from __future__ import annotations

import numpy as np
import pandas as pd

# Quality thresholds on the raw (pre-normalization) sum of midpoints and the average spread.
MAX_OVERROUND_GOOD = 1.10
MAX_SPREAD_GOOD = 0.10


def outcome_mid(bid: float | None, ask: float | None) -> float | None:
    """Bid/ask midpoint; None when the quote is unusable (no ask, or crossed)."""
    if ask is None or (isinstance(ask, float) and np.isnan(ask)) or ask <= 0:
        return None
    b = 0.0 if bid is None or (isinstance(bid, float) and np.isnan(bid)) else bid
    if b > ask:
        return None
    return (b + ask) / 2


def implied_probabilities(outcomes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-outcome normalized probabilities and per-event summary.

    Input: rows with [venue, event, meeting_date, outcome, change_bp, yes_bid, yes_ask].
    Returns (outcomes + [mid, spread, prob], summary per (venue, event)).
    """
    df = outcomes.copy()
    df["mid"] = [outcome_mid(b, a) for b, a in zip(df["yes_bid"], df["yes_ask"], strict=True)]
    df["spread"] = df["yes_ask"] - df["yes_bid"].fillna(0)
    totals = df.groupby(["venue", "event"])["mid"].transform("sum")
    df["prob"] = df["mid"] / totals
    rows = []
    for (venue, event), g in df.groupby(["venue", "event"], sort=False):
        overround = g["mid"].sum()
        exp_bp = float((g["prob"] * g["change_bp"]).sum()) if overround > 0 else np.nan
        spread = float(g["spread"].mean())
        good = overround <= MAX_OVERROUND_GOOD and spread <= MAX_SPREAD_GOOD
        rows.append(
            {
                "venue": venue,
                "event": event,
                "meeting_date": g["meeting_date"].iloc[0],
                "overround": overround,
                "avg_spread": spread,
                "expected_change_bp": exp_bp,
                "quality": "ok" if good else "illiquid",
                "n_quoted": int(g["mid"].notna().sum()),
            }
        )
    return df, pd.DataFrame(rows)
