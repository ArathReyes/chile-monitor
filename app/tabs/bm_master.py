"""Bond Master: every local Treasury bond with outstanding amount (nominal; no DV01)."""

from __future__ import annotations

from datetime import date

import bm_data
import pandas as pd
import streamlit as st
from bm_ui import header, job_caption, money, pending, text_table

from monitor.models import bonds as B


def master_table() -> pd.DataFrame:
    m, o = bm_data.master(), bm_data.outstanding()
    t = m.merge(o, on="nemo", how="left")
    today = date.today()
    u, _ = B.asof_value(bm_data.uf(), today)
    fx, _ = B.asof_value(bm_data.usdclp(), today)
    t["years"] = [B.years_between(today, d) if pd.notna(d) else None for d in t["maturity_date"]]
    t["clp"] = [
        B.to_clp(a, unit, u) if pd.notna(a) else None
        for a, unit in zip(t["amount"], t["unit"], strict=True)
    ]
    t["usd"] = [B.to_usd(c, fx) for c in t["clp"]]
    return t


def render() -> None:
    header(
        "Local Treasury bonds: Hacienda Stock Total (live bonds, outstanding) + Características "
        "Financieras (coupon months); bonds only seen in auctions are decoded from the nemo and "
        "flagged.",
        "bm_master",
        "master",
        "Bond master and outstanding (weekly job)",
    )
    t = master_table()
    if t.empty:
        pending("Bond master not loaded. Press Refresh (runs `monitor.jobs.bond_master_weekly`).")
        return
    live_only = st.toggle("Outstanding bonds only", value=True, key="bm_m_live")
    if live_only:
        t = t[t["amount"].notna() & (t["years"].fillna(-1) > 0)]
    show = pd.DataFrame(
        {
            "Nemo": t["nemo"],
            "ISIN": t["isin"],
            "Type": t["instrument_type"],
            "Ccy": t["currency"],
            "Coupon %": t["coupon_pct"],
            "Coupon months": t["coupon_months"],
            "Issue": t["issue_date"].dt.strftime("%d-%b-%y"),
            "Maturity": t["maturity_date"].dt.strftime("%d-%b-%y"),
            "Years": t["years"],
            "Outstanding": t["amount"],
            "Unit": t["unit"],
            "Outstanding US$": [money(x, "US$") for x in t["usd"]],
            "ESG": t["esg"],
            "Source": t["source"],
            "Flagged": t["flagged"],
        }
    )
    st.dataframe(
        text_table(show, {"Coupon %": "{:.2f}", "Years": "{:.1f}", "Outstanding": "{:,.0f}"}),
        hide_index=True,
        width="stretch",
        height=520,
    )
    tot = t.groupby("currency")["usd"].sum() / 1e9
    st.caption(
        f"{len(t)} bonds. Outstanding ≈ "
        + ", ".join(f"{c} US${v:,.1f}bn" for c, v in tot.items())
        + " at today's UF and dólar observado. Units: MM CLP (Letras, BTP), thousand UF (BTU)."
    )

    st.markdown("**Nominal converter**")
    c1, c2, c3 = st.columns([2, 1.2, 2])
    nemo = c1.selectbox("Bond", t["nemo"].tolist(), key="bm_conv_bond")
    row = t[t["nemo"] == nemo].iloc[0]
    unit = "M UF" if row["currency"] == "UF" else "MM CLP"
    face = c2.number_input(
        f"Face ({unit})", min_value=0.0, value=1000.0, step=100.0, key="bm_conv_face"
    )
    u, ud = B.asof_value(bm_data.uf(), date.today())
    fx, fd = B.asof_value(bm_data.usdclp(), date.today())
    clp = B.to_clp(face, unit, u)
    c3.metric("Nominal", money(clp, "$"), money(B.to_usd(clp, fx), "US$"), delta_color="off")
    st.caption(
        f"UF {u:,.2f} ({ud:%d-%b}) · dólar observado {fx:,.2f} ({fd:%d-%b}). Face value, "
        "not market value (no pricing, by design)."
    )
    job_caption(
        ["hacienda.master"],
        "Hacienda ODP (Stock Total, Características Financieras); BCCh BDE (UF, USD)",
    )
