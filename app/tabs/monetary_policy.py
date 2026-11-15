"""Monetary Policy tab: five placeholder sub-tabs."""

from __future__ import annotations

import streamlit as st
from components import SourceRow, TabSpec, placeholder_card

BDE = "BCCh BDE API"
WEB = "bcentral.cl"

SUBTABS: list[TabSpec] = [
    TabSpec(
        "Overview",
        "A one-screen read of where the TPM is, what the market prices, and what's next.",
        [
            "Current TPM, last decision and vote, next RPM date and countdown",
            "Market-implied move for the next meeting (SPC, Kalshi) against EEE/EOF",
            "Latest statement headline and hawk/dove score",
        ],
        [
            SourceRow("TPM level", BDE, "REST GetSeries", "BCCH_API_TOKEN", "Daily"),
            SourceRow(
                "Next meeting", "config/meetings.yaml", "local YAML", "none", "Annual calendar"
            ),
            SourceRow("Implied move", "BDE SPC + Kalshi", "REST", "BCCH_API_TOKEN", "T-1 / live"),
        ],
    ),
    TabSpec(
        "Board & Communication",
        "Who decides, and what they said, compared statement by statement.",
        [
            "Board members and tenures",
            "Statement archive with a diff viewer vs the previous statement",
            "Minutes archive, vote record and dissents",
            "Hawk/dove score per statement and minutes (Anthropic API, optional)",
        ],
        [
            SourceRow(
                "Board, statements, minutes",
                WEB,
                "scraping (httpx + BS4)",
                "none",
                "Per RPM; minutes ~2 weeks later",
            ),
            SourceRow(
                "Hawk/dove score",
                "Anthropic API",
                "anthropic SDK",
                "ANTHROPIC_API_KEY (opt.)",
                "Per document",
            ),
        ],
    ),
    TabSpec(
        "Decision History",
        "Every decision against consensus, to measure BCCh surprises.",
        [
            "Decision table: date, move, vote, dissents",
            "Surprise vs EEE and EOF consensus",
            "TPM step chart with meeting markers",
        ],
        [
            SourceRow("TPM", BDE, "REST GetSeries", "BCCH_API_TOKEN", "Daily"),
            SourceRow(
                "EEE/EOF expectations", BDE, "REST GetSeries", "BCCH_API_TOKEN", "Monthly / per RPM"
            ),
            SourceRow("Meeting dates", "config/meetings.yaml", "local YAML", "none", "Annual"),
        ],
    ),
    TabSpec(
        "Implied Path & Probabilities",
        "The market-implied TPM path and decision probabilities from swaps and prediction markets.",
        [
            "Implied TPM path from Cámara swaps (SPC, T-1 close; day-count harmonized)",
            "Kalshi KXCBDCHILE probabilities (live) and Polymarket (second source)",
            "Overlay: EEE/EOF medians and the IPoM TPM corridor",
        ],
        [
            SourceRow(
                "SPC 90/180/360d, 1y-10y",
                BDE,
                "REST GetSeries",
                "BCCH_API_TOKEN",
                "Daily, T-1 (360 basis) / close (365 basis)",
            ),
            SourceRow(
                "BCCh decision markets", "Kalshi Trade API v2", "REST (public)", "none", "Live"
            ),
            SourceRow("Decision markets", "Polymarket Gamma API", "REST (VERIFY)", "none", "Live"),
            SourceRow("IPoM corridor", WEB, "PDF scraping", "none", "Quarterly"),
        ],
    ),
    TabSpec(
        "Surveys",
        "What economists (EEE) and traders (EOF) expect for TPM and inflation.",
        [
            "EEE TPM expectations: next meeting, following meeting, 5m, 11m, 23m",
            "EOF pre- and post-RPM rounds",
            "Inflation expectations over time",
        ],
        [
            SourceRow(
                "EEE / EOF results",
                BDE,
                "REST GetSeries",
                "BCCH_API_TOKEN",
                "Monthly / twice per RPM",
            ),
            SourceRow("Survey calendar", WEB, "scraping", "none", "Annual"),
        ],
    ),
]


def render() -> None:
    for tab, spec in zip(st.tabs([s.title for s in SUBTABS]), SUBTABS, strict=True):
        with tab:
            placeholder_card(spec, key=f"mp_{spec.title}")
