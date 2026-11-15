"""Curves & Rates · Curve Snapshot."""

from __future__ import annotations

import cr_charts
import cr_data
import numpy as np
import pandas as pd
import streamlit as st
from cr_ui import CAPTION, WINDOWS, curve_caption, header, no_data, text_table

from monitor.models import curves as C

LABELS = {k: c.label for k, c in C.CURVES.items()}
BY_LABEL = {v: k for k, v in LABELS.items()}


def _apply_table_click() -> None:
    """A click on a percentile-table cell selects that series (applied before the selectbox)."""
    sel = st.session_state.get("cr_pct_table")
    cells = (sel or {}).get("selection", {}).get("cells", []) if sel else []
    if not cells:
        return
    cell = tuple(cells[0])
    if st.session_state.get("cr_pct_applied") == cell:
        return
    st.session_state["cr_pct_applied"] = cell
    col = cell[1]
    if col in BY_LABEL:
        st.session_state["cr_series"] = col


def _overlays(curve: str, ref, show_fit: bool, show_model: bool):
    fit_curve = model_curve = None
    tenors = [float(t) for t in cr_data.panel(curve).columns]
    if show_fit and C.CURVES[curve].fit:
        params, _ = cr_data.fit_params(curve, ref)
        fit_curve = cr_charts.fit_line(params, min(tenors), max(tenors))
    if show_model and curve == "swap_bei":
        pn, _ = cr_data.fit_params("nom_swap", ref)
        pr, _ = cr_data.fit_params("real_swap", ref)
        if pn and pr:
            t = np.linspace(1.0, 10.0, 60)
            model_curve = pd.DataFrame(
                {"tenor": t, "value": (C.nss_yield(t, pn) - C.nss_yield(t, pr)) * 100}
            )
    return fit_curve, model_curve


def _figure(curve: str, opts: dict, height: int = 440, legend: bool = True, title=None):
    panel = cr_data.panel(curve)
    ref = cr_data.ref_date(curve)
    values = opts["mode"] == "Values"
    fit_curve, model_curve = (
        _overlays(curve, ref, opts["fit"], opts["model"]) if values else (None, None)
    )
    return cr_charts.snapshot_figure(
        panel,
        C.CURVES[curve].unit,
        opts["mode"],
        opts["horizons"],
        opts["scale"],
        opts["window"] if values else None,
        ref,
        fit_curve,
        model_curve,
        title=title,
        height=height,
        showlegend=legend,
    )


def percentile_table(window: str) -> pd.DataFrame:
    cols = {}
    for k in C.CURVES:
        p = cr_data.panel(k)
        ref = cr_data.ref_date(k)
        if ref is None:
            continue
        b = C.box_stats(p, ref, window, C.curve_asof(p, ref))
        cols[LABELS[k]] = b.set_index("tenor")["pct"]
    if not cols:
        return pd.DataFrame()
    df = pd.DataFrame(cols).sort_index()
    df.index = [C.tenor_label(t) for t in df.index]
    return df


def _style_pct(v):
    if v is None or pd.isna(v):
        return ""
    if v <= 10:
        return "background-color: rgba(42,120,214,0.30)"
    if v >= 90:
        return "background-color: rgba(227,73,72,0.30)"
    return ""


def delta_table(curve: str, horizons: list[str]) -> pd.DataFrame:
    panel, ref = cr_data.panel(curve), cr_data.ref_date(curve)
    factor = 100.0 if C.CURVES[curve].unit == "pct" else 1.0
    cols = {}
    for h in C.HORIZONS:
        d = C.curve_deltas(panel, ref, h, factor)
        used = max(d["then_date"]) if len(d) else C.horizon_target(ref, h)
        cols[f"{h} ({used:%d-%b})"] = d.set_index("tenor")["delta_bp"]
    df = pd.DataFrame(cols).reindex([float(t) for t in panel.columns])
    df.index = [C.tenor_label(t) for t in df.index]
    return df.dropna(how="all")


def render() -> None:
    header(
        "Observed curve points (never interpolated) vs history. Rates on a 365-day basis; spreads "
        "in bp from same-date legs. Hollow markers: stale (> 3 business days old); points older "
        "than 20 business days are hidden.",
        "cr_snapshot",
    )
    if no_data(None if all(cr_data.panel(k).empty for k in C.CURVES) else "nom_swap"):
        return
    _apply_table_click()
    st.session_state.setdefault("cr_series", LABELS["nom_swap"])
    c1, c2, c3, c4 = st.columns([2.2, 1.3, 2.6, 1.2])
    label = c1.selectbox("Series", list(LABELS.values()), key="cr_series")
    mode = c2.radio("Mode", ["Values", "Deltas"], horizontal=True, key="cr_mode")
    horizons = c3.multiselect("Horizons", list(C.HORIZONS), default=list(C.HORIZONS), key="cr_h")
    window = c4.selectbox(
        "Boxplot window",
        WINDOWS,
        index=WINDOWS.index("6M"),
        key="cr_window",
        disabled=mode != "Values",
    )
    d1, d2, d3, d4 = st.columns([1.6, 1.2, 1.4, 1.8])
    scale = d1.radio("X axis", ["√", "Linear"], horizontal=True, key="cr_scale")
    grid = d2.toggle("Grid (all 7)", key="cr_grid")
    show_fit = d3.toggle("Show NSS fit", key="cr_fit", disabled=mode != "Values")
    curve = BY_LABEL[label]
    show_model = d4.toggle(
        "Show model spreads", key="cr_model", disabled=mode != "Values" or curve not in C.SPREADS
    )
    opts = dict(
        mode=mode,
        horizons=horizons,
        window=window,
        fit=show_fit,
        model=show_model,
        scale="sqrt" if scale == "√" else "linear",
    )

    if grid:
        keys = list(C.CURVES)
        for row in range(0, len(keys), 3):
            cols = st.columns(3)
            for col, k in zip(cols, keys[row : row + 3], strict=False):
                with col:
                    if cr_data.panel(k).empty:
                        st.caption(f"{LABELS[k]}: no data")
                        continue
                    st.plotly_chart(
                        _figure(k, opts, 320, legend=False, title=LABELS[k]),
                        width="stretch",
                        key=f"cr_grid_{k}",
                    )
    else:
        if cr_data.panel(curve).empty:
            no_data(curve)
            return
        st.plotly_chart(_figure(curve, opts), width="stretch", key="cr_main")
        notes = []
        if show_fit and not C.CURVES[curve].fit:
            notes.append("No NSS fit for bond curves (too few live tenors); observed points only.")
        if show_fit and C.CURVES[curve].fit:
            _, fd = cr_data.fit_params(curve, cr_data.ref_date(curve))
            notes.append(f"NSS fit of {fd:%d-%b-%Y}." if fd else "No NSS fit stored yet.")
        if show_model and curve in ("bond_bei", "swap_spread"):
            notes.append("No model version: bond curves aren't fitted.")
        if show_model and curve == "swap_bei":
            notes.append("Model: NSS nominal swap fit minus NSS real swap fit, 1–10Y.")
        if mode == "Values":
            notes.append(f"Boxes: p25–p75 with median line; whiskers min–max over {window}.")
        st.caption(" ".join(notes))
    st.caption(CAPTION)
    curve_caption(list(C.CURVES) if grid else [curve])

    st.markdown(
        f"**Today's percentile within the {window} window** (click a cell to select a series)"
    )
    pct = percentile_table(window)
    if pct.empty:
        st.caption("No data.")
    else:
        st.dataframe(
            text_table(pct, "{:.0f}").style.apply(lambda _: pct.map(_style_pct), axis=None),
            width="stretch",
            key="cr_pct_table",
            on_select="rerun",
            selection_mode="single-cell",
        )
        st.caption("Highlighted: ≤ 10th percentile (blue), ≥ 90th (red).")
    if not grid and not cr_data.panel(curve).empty:
        st.markdown(f"**Changes, {label}** (bp; date = actual observation used)")
        st.dataframe(text_table(delta_table(curve, horizons), "{:+.1f}"), width="stretch")
