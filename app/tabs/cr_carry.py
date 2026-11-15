"""Curves & Rates · Carry & Rolldown."""

from __future__ import annotations

import math

import cm_data
import cr_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import CAT, base_layout
from cr_ui import curve_caption, header, pending, text_table

from monitor.models import curves as C
from monitor.models.uf import uf_accrual_pct
from tabs.cm_seasonality import SCENARIOS

CURVE_COLORS = dict(zip(C.OUTRIGHT, CAT, strict=False))
SHORT = {
    "nom_bond": "Nom bond",
    "nom_swap": "Nom swap",
    "real_bond": "Real bond",
    "real_swap": "Real swap",
}
REAL = ("real_bond", "real_swap")


def _uf_path(name: str, override: str) -> pd.Series | None:
    from tabs.cm_uf import _scenario_path  # shared with Chile Macro · UF & Carry

    uf, last9 = cm_data.uf_known()
    if uf.empty or last9 is None or cm_data.last_cpi_month() is None:
        return None
    path, _, _ = _scenario_path(name, override)
    return path


def _rolled(curve: str, today: pd.DataFrame, tenor: float, h: float, params: dict | None):
    """y_now - (roll in % of yield): fitted curves roll on the NSS fit, bonds linearly.
    No roll when T - h is below the shortest observed tenor (no extrapolation)."""
    y_now = float(today.set_index("tenor").loc[tenor, "value"])
    if tenor - h < float(today["tenor"].min()) - 1e-9:
        return None
    if C.CURVES[curve].fit:
        if not params:
            return None
        roll = float(C.nss_yield(tenor, params)[0] - C.nss_yield(max(tenor - h, 0.0), params)[0])
        return y_now - roll
    return C.interp_linear(list(today["tenor"]), list(today["value"]), tenor - h)


def carry_table(uf_path: pd.Series | None) -> tuple[pd.DataFrame, list[str]]:
    rows, notes = [], []
    for curve in C.OUTRIGHT:
        ref = cr_data.ref_date(curve)
        if ref is None:
            continue
        today = C.curve_asof(cr_data.panel(curve), ref)
        params = None
        if C.CURVES[curve].fit:  # refit on the displayed points (incl. recent stale ones)
            stored, _ = cr_data.fit_params(curve, ref)
            params = C.fit_nss(list(today["tenor"]), list(today["value"]), prev=stored).params
        for _, r in today.iterrows():
            t, row = (
                float(r["tenor"]),
                {
                    "curve": curve,
                    "Instrument": SHORT[curve],
                    "Tenor": C.tenor_label(float(r["tenor"])),
                    "tenor": float(r["tenor"]),
                    "Yield": r["value"],
                    "as of": r["obs_date"],
                },
            )
            for h in ("1M", "3M"):
                f = cr_data.funding(ref, h)
                if f is None:
                    continue
                hy = C.HORIZON_YEARS[h]
                uf = None
                if curve in REAL and uf_path is not None:
                    end = (pd.Timestamp(ref) + pd.DateOffset(months=1 if h == "1M" else 3)).date()
                    try:
                        uf = uf_accrual_pct(uf_path, ref, end)
                    except ValueError:
                        notes.append(f"UF path doesn't reach {end}: UF accrual blank.")
                cr = C.carry_roll(t, r["value"], _rolled(curve, today, t, hy, params), f, hy, uf)
                row.update(
                    {
                        f"Carry {h}": cr.carry_bp,
                        f"Roll {h}": cr.roll_bp,
                        f"UF {h}": cr.uf_bp,
                        f"Total {h}": cr.total_bp,
                    }
                )
            vol = C.realized_vol_bp(cr_data.panel(curve)[t], ref, "3M")
            row["Vol (bp/day)"] = vol
            for h in ("1M", "3M"):
                tot = row.get(f"Total {h}")
                vh = vol * math.sqrt(C.HORIZON_BD[h]) if vol else None
                row[f"Vol {h}"] = vh
                row[f"(C+R)/vol {h}"] = tot / vh if (tot is not None and vh) else None
            rows.append(row)
    return pd.DataFrame(rows), sorted(set(notes))


def scatter(df: pd.DataFrame, h: str) -> go.Figure:
    fig = go.Figure()
    for curve in C.OUTRIGHT:
        d = df[(df["curve"] == curve) & df[f"Total {h}"].notna() & df[f"Vol {h}"].notna()]
        if d.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=d[f"Vol {h}"],
                y=d[f"Total {h}"],
                name=SHORT[curve],
                mode="markers+text",
                text=d["Tenor"],
                textposition="top center",
                marker=dict(color=CURVE_COLORS[curve], size=10, line=dict(width=2, color="white")),
                hovertemplate="%{fullData.name} %{text}: C+R %{y:.1f}bp, vol %{x:.1f}bp"
                + "<extra></extra>",
            )
        )
    fig.add_hline(y=0, line=dict(color="#8b8a85", width=1))
    base_layout(fig, None, f"carry + roll {h} (bp)", 420)
    fig.update_layout(hovermode="closest", xaxis=dict(title=f"realized vol over {h} (bp)"))
    return fig


def render() -> None:
    header(
        "Carry and rolldown per tenor, in bp of yield over the horizon (receiver / long bond, "
        "funded overnight). Rolldown: NSS fit for swaps, linear between observed benchmarks for "
        "bonds (no extrapolation).",
        "cr_carry",
    )
    if all(cr_data.panel(k).empty for k in C.OUTRIGHT):
        pending("Curve data not loaded. Press Refresh.")
        return
    c1, c2, c3 = st.columns([1.3, 1.6, 0.8])
    name = c1.selectbox(
        "CPI scenario (UF accrual, real instruments)",
        [*SCENARIOS.values(), "User override"],
        index=2,
        key="cr_uf_scenario",
    )
    override = c2.text_input(
        "User override: monthly CPI % for the next 3 months",
        "0.3, 0.2, 0.4",
        key="cr_uf_override",
        disabled=name != "User override",
    )
    h = c3.radio("Scatter horizon", ["1M", "3M"], index=1, key="cr_carry_h")
    uf_path = _uf_path(name, override)
    df, notes = carry_table(uf_path)
    if df.empty:
        pending("No funding rate (TPM not loaded). Press Refresh on Monetary Policy.")
        return
    p = cr_data.tpm_path()
    if p["fallback"]:
        st.warning(
            f"Funding: current TPM {p['tpm0']:.2f}% held flat (implied path unavailable: "
            f"{p['fallback']})."
        )
    else:
        st.caption(
            f"**Funding:** SPC-implied TPM path as of {p['as_of']:%d-%b-%Y} "
            f"(Monetary Policy model) + Cámara–TPM spread {p['spread_bp']:.1f}bp, averaged over "
            "each horizon."
        )
    cols = [
        "Instrument",
        "Tenor",
        "Yield",
        "Carry 1M",
        "Roll 1M",
        "UF 1M",
        "Total 1M",
        "Carry 3M",
        "Roll 3M",
        "UF 3M",
        "Total 3M",
        "Vol (bp/day)",
        "(C+R)/vol 1M",
        "(C+R)/vol 3M",
    ]
    fmt = {c: "{:+.1f}" for c in cols[3:11]} | {
        "Yield": "{:.2f}%",
        "Vol (bp/day)": "{:.1f}",
        "(C+R)/vol 1M": "{:+.2f}",
        "(C+R)/vol 3M": "{:+.2f}",
    }
    show = df[[c for c in cols if c in df.columns]].copy()
    for c in show.columns[2:]:
        show[c] = pd.to_numeric(show[c], errors="coerce")  # None -> NaN (blank)
    st.dataframe(
        text_table(show, fmt),
        width="stretch",
        hide_index=True,
        height=min(38 * (len(df) + 1), 900),
    )
    st.caption(
        "carry = (yield − funding) × h / D; roll = y(T) − y(T − h); UF = UF accrual over h / D "
        "(real only, CPI scenario as in Chile Macro); D = par annuity duration. "
        "Total = carry + roll + UF. "
        "Vol: std of daily changes over 3M, scaled by √(21 or 63 business days). Blank roll: T − h "
        "is below the shortest observed tenor (no extrapolation)."
    )
    for n in notes:
        st.caption(n)
    st.plotly_chart(scatter(df, h), width="stretch")
    curve_caption(list(C.OUTRIGHT), "Funding: tpm_implied; UF: models/uf.py.")
