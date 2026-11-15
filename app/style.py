"""Chile Monitor look & feel: header with the Chilean flag, flag-blue sub-tabs, background and
hover feedback. Colors/fonts for widgets come from .streamlit/config.toml; this adds CSS on top.

Flag colors (approximate official tones): azul #0039A6, rojo #D52B1E, blanco #FFFFFF.
"""

from __future__ import annotations

import base64
import html
import random
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

FLAG_BLUE = "#0039A6"
FLAG_RED = "#D52B1E"

# 3:2 flag: blue canton = square of half the height; star centred, diameter = half the canton.
STAR = (
    "50.00,25.00 55.61,42.27 73.78,42.27 59.08,52.95 64.69,70.23 "
    "50.00,59.55 35.31,70.23 40.92,52.95 26.22,42.27 44.39,42.27"
)
_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 200">'
    f'<rect width="300" height="200" fill="{FLAG_RED}"/>'
    '<rect width="300" height="100" fill="#FFFFFF"/>'
    f'<rect width="100" height="100" fill="{FLAG_BLUE}"/>'
    f'<polygon points="{STAR}" fill="#FFFFFF"/></svg>'
)
# st.html sanitizes inline <svg>; an <img> with a data URI survives.
FLAG_IMG = (
    '<img class="cm-flag" width="66" height="44" alt="Flag of Chile" '
    f'src="data:image/svg+xml;base64,{base64.b64encode(_SVG.encode()).decode()}"/>'
)


def _palette() -> dict[str, str]:
    dark = getattr(getattr(st.context, "theme", None), "type", "light") == "dark"
    return {
        # sub-tab text needs a lighter step of the same blue to stay readable on dark surfaces
        "blue_text": "#8fb0f5" if dark else FLAG_BLUE,
        "tint": "rgba(91,141,239,0.16)" if dark else "rgba(0,57,166,0.07)",
        "tint_hover": "rgba(91,141,239,0.26)" if dark else "rgba(0,57,166,0.14)",
        "card": "rgba(255,255,255,0.03)" if dark else "rgba(255,255,255,0.75)",
        "border": "rgba(143,176,245,0.18)" if dark else "rgba(0,57,166,0.12)",
        "shadow": "rgba(0,0,0,0.45)" if dark else "rgba(0,57,166,0.14)",
    }


def _css(p: dict[str, str]) -> str:
    return f"""<style>
/* ---------- background: faint flag-colour glow behind the page ---------- */
.stApp {{
  background-image:
    radial-gradient(1100px 420px at -5% -12%, rgba(0,57,166,0.10), transparent 62%),
    radial-gradient(900px 360px at 105% -14%, rgba(213,43,30,0.07), transparent 60%);
  background-attachment: fixed;
}}
[data-testid="stMainBlockContainer"] {{ padding-top: 4.4rem; }}
[data-testid="stHeader"] {{ background: transparent; }}

/* ---------- header ---------- */
.cm-header {{
  position: relative; overflow: hidden; display: flex; align-items: center; gap: 18px;
  padding: 16px 20px 18px; margin-bottom: 6px; border-radius: 16px;
  border: 1px solid {p["border"]}; background: {p["card"]};
  backdrop-filter: blur(6px); box-shadow: 0 6px 24px -14px {p["shadow"]};
}}
.cm-header::after {{  /* flag stripe: blue canton, white over red */
  content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 5px;
  background:
    linear-gradient(90deg, {FLAG_BLUE} 0 18%, transparent 18%),
    linear-gradient(180deg, #ffffff 0 50%, {FLAG_RED} 50% 100%);
}}
.cm-flag {{
  flex: none; border-radius: 4px; box-shadow: 0 2px 8px rgba(0,0,0,0.18);
  transform-origin: left center; transition: transform .35s ease;
}}
.cm-flag-link {{ display: flex; flex: none; cursor: pointer; line-height: 0; }}
.cm-flag-link:active .cm-flag {{ transform: scale(0.94); }}
.cm-header:hover .cm-flag {{ animation: cm-wave 1.6s ease-in-out infinite; }}
@keyframes cm-wave {{
  0%, 100% {{ transform: perspective(240px) rotateY(0deg) skewY(0deg); }}
  25% {{ transform: perspective(240px) rotateY(-14deg) skewY(-2deg); }}
  75% {{ transform: perspective(240px) rotateY(10deg) skewY(1.5deg); }}
}}
.cm-title {{
  margin: 0; font-size: 2.05rem; font-weight: 700; letter-spacing: -0.025em; line-height: 1.1;
}}
.cm-title span {{ color: {p["blue_text"]}; }}
.cm-sub {{ margin-top: 4px; font-size: .84rem; opacity: .72; letter-spacing: .01em; }}
.cm-dot {{
  display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px;
  background: #1baf7a; box-shadow: 0 0 0 0 rgba(27,175,122,.6); animation: cm-pulse 2.4s infinite;
}}
@keyframes cm-pulse {{
  0% {{ box-shadow: 0 0 0 0 rgba(27,175,122,.55); }}
  70% {{ box-shadow: 0 0 0 7px rgba(27,175,122,0); }}
  100% {{ box-shadow: 0 0 0 0 rgba(27,175,122,0); }}
}}

/* ---------- main tabs (Streamlit 1.67: role=tablist > [data-testid=stTab]) ---------- */
[data-testid="stTabs"] [role="tablist"] {{ gap: 2px; }}
[data-testid="stTabs"] [data-testid="stTab"] {{
  border-radius: 10px 10px 0 0; padding-left: 12px; padding-right: 12px;
  transition: color .15s ease, background-color .15s ease;
}}
[data-testid="stTabs"] [data-testid="stTab"] p {{ font-weight: 600; }}
[data-testid="stTabs"] [data-testid="stTab"]:hover {{
  color: {p["blue_text"]}; background: {p["tint"]};
}}

/* ---------- sub-tabs (tabs inside a tab): Chilean flag-blue pills ---------- */
[data-testid="stTabs"] [data-testid="stTabs"] [role="tablist"] {{
  gap: 6px; padding: 5px; width: fit-content; max-width: 100%; flex-wrap: wrap;
  border: none; box-shadow: none; border-radius: 999px; background: {p["tint"]};
  margin-bottom: 6px;
}}
[data-testid="stTabs"] [data-testid="stTabs"] [role="tablist"]::before,
[data-testid="stTabs"] [data-testid="stTabs"] [role="tablist"]::after,
[data-testid="stTabs"] [data-testid="stTabs"] [data-testid="stTab"]::before,
[data-testid="stTabs"] [data-testid="stTabs"] [data-testid="stTab"]::after,
[data-testid="stTabs"] [data-testid="stTabs"] .react-aria-SelectionIndicator {{ display: none; }}
[data-testid="stTabs"] [data-testid="stTabs"] [data-testid="stTab"] {{
  height: auto; min-height: 0; padding: 5px 15px; border: none; border-radius: 999px;
  color: {p["blue_text"]};
  transition: background-color .18s ease, color .18s ease, transform .18s ease,
    box-shadow .18s ease;
}}
[data-testid="stTabs"] [data-testid="stTabs"] [data-testid="stTab"] p {{ font-size: .9rem; }}
[data-testid="stTabs"] [data-testid="stTabs"]
  [data-testid="stTab"]:hover:not([aria-selected="true"]) {{
  background: {p["tint_hover"]}; color: {p["blue_text"]}; transform: translateY(-1px);
}}
[data-testid="stTabs"] [data-testid="stTabs"] [data-testid="stTab"][aria-selected="true"] {{
  background: {FLAG_BLUE}; color: #ffffff; box-shadow: 0 4px 12px -2px rgba(0,57,166,0.45);
}}

/* ---------- cards that react to the mouse ---------- */
[data-testid="stMetric"] {{
  padding: 12px 14px; border-radius: 14px; border: 1px solid {p["border"]};
  background: {p["card"]};
  transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
}}
[data-testid="stMetric"]:hover {{
  transform: translateY(-3px); border-color: {p["blue_text"]};
  box-shadow: 0 10px 24px -12px {p["shadow"]};
}}
[data-testid="stPlotlyChart"], [data-testid="stDataFrame"] {{
  border-radius: 14px; transition: box-shadow .2s ease;
}}
[data-testid="stPlotlyChart"]:hover, [data-testid="stDataFrame"]:hover {{
  box-shadow: 0 0 0 1px {p["border"]}, 0 12px 28px -18px {p["shadow"]};
}}
.stButton > button {{
  border-radius: 999px; font-weight: 600;
  transition: transform .15s ease, box-shadow .15s ease, background-color .15s ease;
}}
.stButton > button:hover {{
  transform: translateY(-1px); box-shadow: 0 6px 14px -8px {p["shadow"]};
}}
.stButton > button:active {{ transform: translateY(0); }}
[data-testid="stExpander"] details {{ border-radius: 12px; transition: border-color .15s ease; }}
[data-testid="stExpander"] details:hover {{ border-color: {p["blue_text"]}; }}

@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{ animation: none !important; transition: none !important; }}
}}
</style>"""


def apply() -> None:
    """Inject the stylesheet (call once per run, before rendering content)."""
    st.html(_css(_palette()))


# Easter egg: the flag links to one of these, picked at random on every render (st.html strips
# JavaScript, so the choice is made server-side; each rerun re-rolls it).
FLAG_LINKS = (
    "https://www.youtube.com/watch?v=UEqTIwRrWvA&list=RDUEqTIwRrWvA&start_radio=1",
    "https://www.youtube.com/watch?v=ChKqrwa7Ylw&list=RDChKqrwa7Ylw&start_radio=1",
    "https://www.youtube.com/watch?v=Dy4pEFFbFsA&list=RDDy4pEFFbFsA&start_radio=1",
)


def flag_link(rng: random.Random | None = None) -> str:
    url = html.escape((rng or random).choice(FLAG_LINKS), quote=True)
    attrs = f'class="cm-flag-link" href="{url}" target="_blank" rel="noopener noreferrer"'
    return f"<a {attrs}>{FLAG_IMG}</a>"


def header() -> None:
    now_ny = datetime.now(ZoneInfo("America/New_York"))
    now_scl = now_ny.astimezone(ZoneInfo("America/Santiago"))
    st.html(
        f"""<div class="cm-header">{flag_link()}<div>
<h1 class="cm-title">Chile <span>Monitor</span></h1>
<div class="cm-sub"><span class="cm-dot"></span>Public data only · {now_ny:%a %d %b %Y %H:%M} NY
 · {now_scl:%H:%M} Santiago</div></div></div>"""
    )
