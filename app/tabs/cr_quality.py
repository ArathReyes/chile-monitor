"""Curves & Rates · Data Quality: observed vs missing points, staleness, NSS diagnostics."""

from __future__ import annotations

from datetime import date, timedelta

import cr_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import CAT, base_layout, line
from cr_ui import header, pending

from monitor.cl_calendar import business_days
from monitor.models import curves as C


def coverage(curve: str, end: date) -> pd.DataFrame:
    p = cr_data.panel(curve)
    bdays = business_days(end - timedelta(days=365), end + timedelta(days=1))
    rows = []
    for t in p.columns:
        s = p[t].dropna()
        last = s.index[-1].date() if len(s) else None
        n = int(s.index.isin(pd.to_datetime(bdays)).sum())
        age = C.bd_age(last, end) if last else None
        status = (
            ("hidden" if age > C.MAX_AGE_BD else "stale" if age > C.STALE_BD else "ok")
            if last
            else "no data"
        )
        rows.append(
            {
                "Curve": C.CURVES[curve].label,
                "Tenor": C.tenor_label(float(t)),
                "% days observed (1Y)": 100 * n / len(bdays),
                "last obs": last,
                "age (bd)": age,
                "status": status,
            }
        )
    return pd.DataFrame(rows)


def heatmap(curve: str, end: date) -> go.Figure:
    p = cr_data.panel(curve)
    days = pd.to_datetime(business_days(end - timedelta(days=92), end + timedelta(days=1)))
    z = [[1 if (d in p.index and pd.notna(p.loc[d, t])) else 0 for d in days] for t in p.columns]
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=days,
            y=[C.tenor_label(float(t)) for t in p.columns],
            zmin=0,
            zmax=1,
            colorscale=[[0, "rgba(139,138,133,0.15)"], [1, CAT[0]]],
            showscale=False,
            xgap=1,
            ygap=2,
            customdata=[["observed" if v else "missing" for v in row] for row in z],
            hovertemplate="%{y} · %{x|%d-%b-%y}: %{customdata}<extra></extra>",
        )
    )
    base_layout(
        fig,
        f"{C.CURVES[curve].label}: observed (blue) vs missing, last 3M",
        "",
        60 + 32 * len(p.columns),
    )
    fig.update_layout(hovermode="closest")
    return fig


def render() -> None:
    header("Which points traded, how fresh they are, and how well the NSS fits behave.", "cr_dq")
    if all(cr_data.panel(k).empty for k in C.OUTRIGHT):
        pending("Curve data not loaded. Press Refresh.")
        return
    end = max(d for k in C.OUTRIGHT if (d := cr_data.ref_date(k)))
    curve = st.selectbox(
        "Curve", list(C.CURVES), format_func=lambda k: C.CURVES[k].label, key="cr_dq_curve"
    )
    if not cr_data.panel(curve).empty:
        st.plotly_chart(heatmap(curve, end), width="stretch")
    cov = pd.concat(
        [coverage(k, end) for k in C.CURVES if not cr_data.panel(k).empty], ignore_index=True
    )
    st.markdown(
        f"**Coverage and staleness** (reference date {end:%d-%b-%Y}; stale > {C.STALE_BD} bd, "
        f"hidden > {C.MAX_AGE_BD} bd)"
    )
    st.dataframe(
        cov.style.format({"% days observed (1Y)": "{:.0f}%", "age (bd)": "{:.0f}"}, na_rep=""),
        width="stretch",
        hide_index=True,
    )

    ph = cr_data.placeholders()
    if len(ph):
        st.markdown("**Bad prints dropped** (exact zero > 50bp from both neighbours; not replaced)")
        st.dataframe(ph, hide_index=True, width="stretch")

    st.markdown(
        "**NSS fit diagnostics** (swap curves; bond curves are not fitted: ≤ 3 live tenors)"
    )
    fig = go.Figure()
    skipped = []
    for curve_key, color in (("nom_swap", CAT[0]), ("real_swap", CAT[1])):
        f = cr_data.fits(curve_key)
        if f.empty:
            continue
        ok = f[f["params_json"].notna()]
        ok = ok[ok["obs_date"] >= ok["obs_date"].max() - pd.DateOffset(years=2)]
        line(fig, ok["obs_date"], ok["rmse"], f"{C.CURVES[curve_key].label} RMSE", color)
        sk = f[f["params_json"].isna()]
        if len(sk):
            skipped.append(
                sk.assign(curve=C.CURVES[curve_key].label)[
                    ["curve", "obs_date", "n_points", "note"]
                ]
            )
    if not fig.data:
        pending("No NSS fits stored yet. Press Refresh.")
        return
    st.plotly_chart(base_layout(fig, "Fit RMSE, last 2Y", "bp", 320), width="stretch")
    if skipped:
        sk = pd.concat(skipped)
        st.markdown(f"Days skipped: **{len(sk)}**")
        st.dataframe(
            sk.groupby(["curve", "note"]).size().rename("days").reset_index(), hide_index=True
        )
    else:
        st.caption("No skipped days (every date had ≥ 4 observed points).")
