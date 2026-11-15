"""Chile Macro · UF & Carry."""

from __future__ import annotations

from datetime import date, timedelta

import cm_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import ROLE, base_layout
from cm_ui import header, pending, series_caption
from dateutil.relativedelta import relativedelta

from monitor.models.uf import breakeven_monthly_cpi, path_with_scenario, uf_carry
from tabs.cm_seasonality import SCENARIOS, nowcast, scenario_dict

PAIRS = {
    "BCU/BTU vs BCP/BTP (secondary yields)": {
        2: ("cl_bcu_2y", "cl_bcp_2y"),
        5: ("cl_bcu_5y", "cl_bcp_5y"),
        10: ("cl_bcu_10y", "cl_bcp_10y"),
    },
    "Cámara swap UF vs Cámara swap CLP": {
        2: ("cl_spc_uf_2y", "cl_spc_nom_2y"),
        5: ("cl_spc_uf_5y", "cl_spc_nom_5y"),
        10: ("cl_spc_uf_10y", "cl_spc_nom_10y"),
    },
}


def _scenario_path(name: str, override: str) -> tuple[pd.Series, date, dict]:
    """Known UF + 3 projected periods. Each period's CPI: published actual if available, else the
    scenario (nowcast table), else the seasonal mean."""
    from monitor.models.uf import cpi_month_for_period

    uf, last9 = cm_data.uf_known()
    st_df = cm_data.seasonal()
    table = nowcast(st_df)
    if name == "User override":
        vals = [float(x) for x in override.replace(";", ",").split(",") if x.strip()]
        scen = {m: vals[i] for i, m in enumerate(table["month"]) if i < len(vals)}
    else:
        col = {v: k for k, v in SCENARIOS.items()}[name]
        scen = scenario_dict(table, col, st_df)
    actual = cm_data.series("cl_cpi_mm_hist")
    needed = [
        pd.Timestamp(
            cpi_month_for_period(pd.Timestamp(last9 + relativedelta(months=i)).date())
        ).date()
        for i in range(3)
    ]
    sc = {}
    for m in needed:
        ts = pd.Timestamp(m)
        if ts in actual.index:
            sc[m] = float(actual.loc[ts])
        elif m in scen:
            sc[m] = scen[m]
        else:
            sc[m] = float(st_df.loc[m.month, "mean"])
    path = path_with_scenario(uf, last9, sc, len(sc))
    return path, last9, sc


def calendar(path: pd.Series, last9: date, sc: dict) -> None:
    today = pd.Timestamp(date.today())
    view = path[path.index >= today - pd.Timedelta(days=3)]
    known = view[view.index <= pd.Timestamp(last9)]
    proj = view[view.index > pd.Timestamp(last9)]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=known.index,
            y=known.values,
            name="Known (fixed by last CPI)",
            line=dict(color=ROLE["market"], width=2),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=proj.index,
            y=proj.values,
            name="Projected (scenario)",
            line=dict(color=ROLE["eof"], width=2, dash="dot"),
        )
    )
    st.plotly_chart(base_layout(fig, "UF daily path", "CLP", 320), width="stretch")
    st.caption(
        f"Known through {last9:%d %b %Y} (published by the BCCh). Projection uses CPI m/m of "
        + ", ".join(f"{m:%b-%y} {v:.2f}%" for m, v in sc.items())
        + ". UF rule reproduced exactly on all BDE history since 2012 (see models/uf.py)."
    )
    with st.expander("UF calendar (daily values)"):
        st.dataframe(
            view.rename("UF")
            .to_frame()
            .assign(
                status=lambda d: [
                    "known" if i <= pd.Timestamp(last9) else "projected" for i in d.index
                ]
            )
            .round(2),
            width="stretch",
        )


def calculator(path: pd.Series, last9: date, sc: dict) -> None:
    st.markdown("#### Carry calculator (accrual only: no roll-down or price change)")
    c = st.columns(4)
    pair = c[0].selectbox("Pair", list(PAIRS), key="cm_carry_pair")
    tenor = c[1].selectbox("Tenor (years)", list(PAIRS[pair]), index=0, key="cm_carry_tenor")
    real_id, nom_id = PAIRS[pair][tenor]
    real_s, nom_s = cm_data.series(real_id), cm_data.series(nom_id)
    real_def = float(real_s.iloc[-1]) if len(real_s) else 2.0
    nom_def = float(nom_s.iloc[-1]) if len(nom_s) else 5.0
    real = c[2].number_input(
        "Real yield %", value=round(real_def, 3), step=0.01, format="%.3f", key=f"cm_real_{real_id}"
    )
    nom = c[3].number_input(
        "Nominal yield %", value=round(nom_def, 3), step=0.01, format="%.3f", key=f"cm_nom_{nom_id}"
    )
    c = st.columns(4)
    start = c[0].date_input("Start", value=date.today(), key="cm_carry_start")
    end = c[1].date_input(
        "End",
        value=last9,
        key="cm_carry_end",
        max_value=path.index.max().date(),
        min_value=start + timedelta(days=1),
    )
    notional = (
        c[2].number_input("Notional (CLP mn)", value=1000.0, step=100.0, key="cm_notional") * 1e6
    )
    basis = c[3].selectbox(
        "Day count",
        [365, 360],
        index=0,
        key="cm_basis",
        help="BDE yields and SPC tenor-table rates are quoted on a 365 basis.",
    )
    if pd.Timestamp(start) not in path.index or pd.Timestamp(end) not in path.index:
        st.error("Start and end must be within the UF path shown.")
        return
    r = uf_carry(path, start, end, real, nom, notional, basis)
    m = st.columns(4)
    m[0].metric(
        "UF accrual", f"{r.uf_accrual_pct * 100:.1f}bp", f"{r.days} days", delta_color="off"
    )
    m[1].metric(
        "Real − nominal carry",
        f"{r.diff_bp:+.1f}bp",
        f"{r.diff_bp_annualized:+.0f}bp annualized",
        delta_color="off",
    )
    m[2].metric("In CLP", f"{r.diff_clp:+,.0f}", delta_color="off")
    if pd.Timestamp(end) > pd.Timestamp(last9):
        be = breakeven_monthly_cpi(
            path[path.index <= pd.Timestamp(last9)], last9, start, end, real, nom, basis
        )
        m[3].metric("Breakeven monthly CPI", f"{be:.2f}%", "for unknown months", delta_color="off")
    else:
        m[3].metric("Breakeven monthly CPI", "n/a", "inside known UF window", delta_color="off")
    st.caption(
        f"Defaults: latest BDE {real_id} ({real_s.index[-1]:%d %b}) and {nom_id} "
        f"({nom_s.index[-1]:%d %b}). Real leg = (1 + r·τ)·UF_end/UF_start − 1; nominal = r·τ."
    )
    sensitivity(path, last9, sc, start, end, real, nom, notional, basis)


def sensitivity(path, last9, sc, start, end, real, nom, notional, basis) -> None:
    if pd.Timestamp(end) <= pd.Timestamp(last9):
        st.caption("Sensitivity to CPI: none — the holding period is inside the known UF window.")
        return
    known = path[path.index <= pd.Timestamp(last9)]
    rows = []
    for shift in (-0.20, -0.10, 0.0, 0.10, 0.20):
        p = path_with_scenario(known, last9, {k: v + shift for k, v in sc.items()}, len(sc))
        res = uf_carry(p, start, end, real, nom, notional, basis)
        rows.append(
            {
                "CPI m/m shift": f"{shift * 100:+.0f}bp",
                "Carry diff (bp)": round(res.diff_bp, 1),
                "CLP": round(res.diff_clp),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def accrual_history() -> go.Figure:
    uf = cm_data.series("cl_uf")
    nines = uf[uf.index.day == 9]
    acc = (nines / nines.shift(1) - 1) * 100
    acc = acc[acc.index >= pd.Timestamp(date.today().year - 3, 1, 1)].dropna()
    fig = go.Figure(
        go.Bar(
            x=acc.index,
            y=acc.values,
            marker_color=ROLE["market"],
            hovertemplate="9th %{x|%b %Y}: %{y:.2f}%<extra></extra>",
        )
    )
    fig.update_layout(showlegend=False)
    return base_layout(fig, "Monthly UF accrual (9th to 9th = CPI of two months earlier)", "%", 280)


def render() -> None:
    header(
        "UF path, real vs nominal carry. Sources: BCCh BDE (UF, CPI, BCP/BCU yields, "
        "SPC UF/CLP), EEE, EOF.",
        "cm_uf",
    )
    uf, last9 = cm_data.uf_known()
    if uf.empty or last9 is None or cm_data.last_cpi_month() is None:
        pending("UF data not loaded. Press Refresh.")
        return
    c1, c2 = st.columns([1, 2])
    name = c1.selectbox(
        "CPI scenario for projected months",
        [*SCENARIOS.values(), "User override"],
        index=2,
        key="cm_uf_scenario",
    )
    override = c2.text_input(
        "User override: monthly CPI % for the next 3 months (comma-separated)",
        "0.3, 0.2, 0.4",
        key="cm_uf_override",
        disabled=name != "User override",
    )
    path, last9, sc = _scenario_path(name, override)
    calendar(path, last9, sc)
    calculator(path, last9, sc)
    st.plotly_chart(accrual_history(), width="stretch")
    series_caption(["cl_uf", "cl_cpi_mm_hist", "cl_bcu_2y", "cl_bcp_2y"])
