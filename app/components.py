"""Shared Streamlit UI pieces."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SourceRow:
    feature: str
    source: str
    access: str
    key: str
    frequency: str


@dataclass(frozen=True)
class TabSpec:
    title: str
    purpose: str
    features: list[str]
    sources: list[SourceRow] = field(default_factory=list)
    spec: str = "docs/specs/"


def source_badge(source: str, as_of: datetime | None) -> None:
    """Caption with data source and as-of time (as_of is UTC-naive or tz-aware)."""
    if as_of is None:
        st.caption(f"Source: {source} · as of: no data")
        return
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=ZoneInfo("UTC"))
    st.caption(f"Source: {source} · as of {as_of.astimezone(NY):%Y-%m-%d %H:%M} NY")


def stale_badge(since: datetime | None) -> None:
    label = "never fetched" if since is None else f"stale since {since:%Y-%m-%d}"
    st.markdown(f":orange-badge[⚠ {label}]")


def refresh_button(key: str, disabled: bool = True) -> bool:
    help_text = "Not implemented yet, see docs/specs/" if disabled else "Re-fetch this tab's data"
    return st.button("🔄 Refresh", key=f"refresh_{key}", disabled=disabled, help=help_text)


def placeholder_card(spec: TabSpec, key: str) -> None:
    with st.container(border=True):
        top, right = st.columns([5, 1])
        with top:
            st.subheader(spec.title)
            st.markdown(f"**Purpose.** {spec.purpose}")
        with right:
            refresh_button(key, disabled=True)

        st.markdown("**Planned features**")
        st.markdown("\n".join(f"- {f}" for f in spec.features))

        if spec.sources:
            st.markdown("**Data sources**")
            df = pd.DataFrame(
                [vars(r) for r in spec.sources],
            ).rename(
                columns={
                    "feature": "Feature",
                    "source": "Source",
                    "access": "Access",
                    "key": "Key needed",
                    "frequency": "Frequency / lag",
                }
            )
            st.dataframe(df, hide_index=True, width="stretch")

        st.info(f"**Status:** Not implemented — see `{spec.spec}`", icon="🚧")
