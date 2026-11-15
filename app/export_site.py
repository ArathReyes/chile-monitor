"""Static site: run the real dashboard once (Streamlit's AppTest) and save every tab as HTML.

Nothing is re-implemented: the same tab code, data layer and Plotly figures produce the pages.
What a static page can't do is dropped or frozen: Refresh buttons are omitted, and filters and
pickers show the value they had (their defaults) as a chip. Plotly charts stay interactive.

    uv run python app/export_site.py --out site        # then open site/index.html

One page per top-level tab (index.html = the first); sub-tabs switch in the browser.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from plotly.offline import get_plotlyjs_version

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
NY = ZoneInfo("America/New_York")
SCL = ZoneInfo("America/Santiago")

# ------------------------------------------------------------------ media capture (st.image)
# AppTest keeps media in a mock in-memory store that is discarded after the run; record every
# file as it is added so images can be embedded as data URIs.
MEDIA: dict[str, tuple[bytes, str]] = {}


def _capture_media() -> None:
    from streamlit.runtime.memory_media_file_storage import MemoryMediaFileStorage

    orig = MemoryMediaFileStorage.load_and_get_id

    def load_and_get_id(self, *a, **kw):
        fid = orig(self, *a, **kw)
        f = self._files_by_id[fid]
        MEDIA[fid] = (f.content, f.mimetype)
        return fid

    MemoryMediaFileStorage.load_and_get_id = load_and_get_id


# ------------------------------------------------------------------ markdown (subset Streamlit uses)

_COLOR = {
    "red": "#cf222e",
    "blue": "#0039A6",
    "green": "#1a7f37",
    "orange": "#bc4c00",
    "gray": "#57606a",
    "grey": "#57606a",
    "violet": "#8250df",
    "primary": "#0039A6",
}


def _inline(s: str, allow_html: bool) -> str:
    codes: list[str] = []

    def keep(m: re.Match) -> str:
        codes.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00{len(codes) - 1}\x00"

    s = re.sub(r"`([^`]+)`", keep, s)
    if not allow_html:
        s = html.escape(s, quote=False)
    s = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', s
    )
    s = re.sub(
        r"(?<![\w\"'=/])(https?://[^\s<)]+)",
        r'<a href="\1" target="_blank" rel="noopener">\1</a>',
        s,
    )
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])", r"<em>\1</em>", s)
    s = re.sub(
        r":(\w+)\[([^\]]*)\]",
        lambda m: f'<span style="color:{_COLOR.get(m.group(1), "inherit")}">{m.group(2)}</span>',
        s,
    )
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], s)


def markdown(text: str, allow_html: bool = False) -> str:
    out: list[str] = []
    lines = text.strip("\n").split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        st = ln.strip()
        if not st:
            i += 1
            continue
        if m := re.match(r"^(#{1,6})\s+(.*)$", st):
            n = min(len(m.group(1)) + 1, 6)
            out.append(f"<h{n}>{_inline(m.group(2), allow_html)}</h{n}>")
            i += 1
        elif st.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            head, *body = rows
            th = "".join(f"<th>{_inline(c, allow_html)}</th>" for c in head)
            tb = "".join(
                "<tr>" + "".join(f"<td>{_inline(c, allow_html)}</td>" for c in r) + "</tr>"
                for r in body
            )
            out.append(
                f'<div class="tbl"><table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table></div>'
            )
        elif re.match(r"^([-*]|\d+\.)\s+", st):
            tag = "ol" if re.match(r"^\d+\.", st) else "ul"
            items: list[str] = []
            while i < len(lines) and lines[i].strip():
                s2 = lines[i].strip()
                if m2 := re.match(r"^([-*]|\d+\.)\s+(.*)$", s2):
                    items.append(m2.group(2))
                elif items:
                    items[-1] += " " + s2
                i += 1
            lis = "".join(f"<li>{_inline(x, allow_html)}</li>" for x in items)
            out.append(f"<{tag}>{lis}</{tag}>")
        else:
            para = []
            while (
                i < len(lines)
                and lines[i].strip()
                and not re.match(r"^(#{1,6}\s|\||[-*]\s|\d+\.\s)", lines[i].strip())
            ):
                para.append(lines[i])
                i += 1
            joined = "".join(
                (p.rstrip() + ("<br>" if p.endswith("  ") or p.endswith("\\") else " "))
                for p in para
            )
            out.append(f"<p>{_inline(joined.strip(), allow_html)}</p>")
    return "\n".join(out)


# ------------------------------------------------------------------ elements


def _fmt_cell(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if v != 0 and abs(v) < 0.01:
            return f"{v:.4g}"
        return f"{v:,.2f}"
    if isinstance(v, pd.Timestamp | datetime):
        ts = pd.Timestamp(v)
        return ts.strftime("%Y-%m-%d") if ts == ts.normalize() else ts.strftime("%Y-%m-%d %H:%M")
    return html.escape(str(v))


def dataframe(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return '<p class="muted">(empty table)</p>'
    show_index = not isinstance(df.index, pd.RangeIndex)
    cols = ([df.index.name or ""] if show_index else []) + [str(c) for c in df.columns]
    th = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    rows = []
    for idx, r in zip(df.index, df.itertuples(index=False), strict=True):
        cells = ([_fmt_cell(idx)] if show_index else []) + [_fmt_cell(v) for v in r]
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return f'<div class="tbl"><table><thead><tr>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


class Renderer:
    def __init__(self) -> None:
        self.n_plots = 0
        self.unknown: dict[str, int] = {}
        self.path: list[str] = []
        self.placeholders = True  # dynamic sections → placeholder (filled after the variant runs)
        self.dyn: Dynamic | None = None  # set while rendering a variant: its widgets go live

    def children(self, node) -> str:
        return "".join(self.node(c) for c in getattr(node, "children", {}).values())

    def node(self, n) -> str:  # noqa: C901, PLR0911, PLR0912  (one branch per element type)
        t = getattr(n, "type", "")
        if t in ("flex_container", "vertical", "horizontal") or type(n).__name__ == "Block":
            kids = list(getattr(n, "children", {}).values())
            if kids and all(getattr(k, "type", "") == "column" for k in kids):
                w = [max(float(getattr(k, "weight", 1) or 1), 0.01) for k in kids]
                tpl = " ".join(f"minmax(0,{x:.3f}fr)" for x in w)
                inner = "".join(f'<div class="col">{self.children(k)}</div>' for k in kids)
                return f'<div class="cols" style="grid-template-columns:{tpl}">{inner}</div>'
            if t == "tab_container":
                return self.tabs(n)
            return self.children(n)
        if t == "tab_container":
            return self.tabs(n)
        if t == "column":
            return f'<div class="col">{self.children(n)}</div>'
        if t == "expander":
            return (
                f'<details class="exp"><summary>{_inline(n.label, False)}</summary>'
                f"{self.children(n)}</details>"
            )
        if t in ("markdown", "caption"):
            p = n.proto
            body = markdown(p.body, getattr(p, "allow_html", False))
            if t == "caption" or getattr(p, "element_type", 0) == 2:
                return f'<div class="caption">{body}</div>'
            return f'<div class="md">{body}</div>'
        if t in ("title", "header", "subheader"):
            lvl = {"title": 2, "header": 3, "subheader": 4}[t]
            return f"<h{lvl}>{_inline(n.proto.body, False)}</h{lvl}>"
        if t in ("info", "warning", "success", "error"):
            return f'<div class="alert {t}">{markdown(n.proto.body)}</div>'
        if t == "metric":
            delta = n.delta or ""
            cls = "neg" if delta.strip().startswith(("-", "−")) else "pos"
            if getattr(n.proto, "color", None) in (2,) or "off" in str(n.color):
                cls = "off"
            d = f'<div class="delta {cls}">{html.escape(delta)}</div>' if delta else ""
            return (
                f'<div class="metric"><div class="label">{_inline(n.label, False)}</div>'
                f'<div class="value">{html.escape(n.proto.body)}</div>{d}</div>'
            )
        if t == "dataframe":
            return dataframe(n.value)
        if t == "plotly_chart":
            return self.plot(n.proto)
        if t == "image":
            return self.image(n.proto)
        if t == "html":
            body = n.proto.body
            if "cm-header" in body or body.lstrip().startswith("<style"):
                return ""  # replaced by the static header / stylesheet
            return f'<div class="html">{body}</div>'
        if t == "divider":
            return "<hr>"
        if t == "progress":
            v = int(getattr(n.proto, "value", 0))
            return f'<div class="progress"><span style="width:{v}%"></span></div>'
        if t == "button":
            return ""  # Refresh etc.: nothing to run on a static page
        if t in WIDGETS:
            return self.widget(n)
        if t == "exception":
            return f'<div class="alert error">{html.escape(str(n.value))}</div>'
        if t in ("empty", "toast", "balloons", "snow", "event", "sidebar", "main", "root"):
            return self.children(n)
        self.unknown[t] = self.unknown.get(t, 0) + 1
        return self.children(n)

    def widget(self, n) -> str:
        key = getattr(n, "key", None)
        if self.dyn is not None and key in self.dyn.live_keys:
            return self.live(n, "server" if key in self.dyn.server else "client")
        v = getattr(n, "value", None)
        if isinstance(v, list | tuple):
            v = ", ".join(_fmt_cell(x) if not isinstance(x, str) else x for x in v) or "all"
        elif isinstance(v, bool):
            v = "on" if v else "off"
        elif v is None or v == "":
            return ""
        else:
            v = _fmt_cell(v) if not isinstance(v, str) else v
        label = getattr(n, "label", "") or ""
        return (
            f'<span class="chip" title="Fixed on the static page"><b>{_inline(label, False)}</b> '
            f"{html.escape(str(v))}</span>"
        )

    def live(self, n, role: str) -> str:
        """A working control. 'server' controls pick a pre-rendered variant; 'client' controls
        show/hide chart traces in the browser."""
        t, key, label = n.type, n.key, _inline(n.label or "", False)
        dis = " disabled" if getattr(n.proto, "disabled", False) else ""
        attrs = f'data-k="{html.escape(key)}" data-role="{role}"'
        if t == "toggle" or t == "checkbox":
            chk = " checked" if self.dyn.initial(key, n.value) else ""
            return (
                f'<label class="ctl tog"><input type="checkbox" {attrs}{chk}{dis}>'
                f'<span class="sw"></span>{label}</label>'
            )
        multi = t == "multiselect"
        cur = self.dyn.initial(key, n.value)
        sel = set(cur) if multi else {cur}
        if t == "selectbox":
            opts = "".join(
                f'<option value="{html.escape(json.dumps(o, ensure_ascii=False))}"'
                f"{' selected' if o in sel else ''}>{html.escape(str(o))}</option>"
                for o in n.options
            )
            return f'<label class="ctl"><span>{label}</span><select {attrs}{dis}>{opts}</select></label>'
        btns = "".join(
            f'<button type="button" data-v="{html.escape(json.dumps(o, ensure_ascii=False))}" '
            f'aria-pressed="{str(o in sel).lower()}"{dis}>{html.escape(str(o))}</button>'
            for o in n.options
        )
        kind = "multi" if multi else "one"
        return f'<div class="ctl"><span>{label}</span><div class="seg" {attrs} data-kind="{kind}">{btns}</div></div>'

    def tabs(self, n) -> str:
        kids = [k for k in n.children.values() if getattr(k, "type", "") == "tab"]
        btns = "".join(
            f'<button role="tab" aria-selected="{str(i == 0).lower()}">{html.escape(k.label)}</button>'
            for i, k in enumerate(kids)
        )
        panels = "".join(
            f'<div class="panel"{"" if i == 0 else " hidden"}>{self.panel(k)}</div>'
            for i, k in enumerate(kids)
        )
        return f'<div class="tabs"><div class="tablist" role="tablist">{btns}</div>{panels}</div>'

    def panel(self, tab) -> str:
        self.path.append(tab.label)
        try:
            d = DYNAMIC.get(tuple(self.path))
            if d is not None and self.placeholders:
                return f"<!--DYN:{d.id}-->"
            return self.children(tab)
        finally:
            self.path.pop()

    def plot(self, p) -> str:
        self.n_plots += 1
        spec = p.spec.replace("</", "<\\/")
        return (
            f'<div class="plot" id="p{self.n_plots}"></div>'
            f'<script type="application/json" data-for="p{self.n_plots}">{spec}</script>'
        )

    def image(self, p) -> str:
        out = []
        for img in p.imgs:
            fid = Path(img.url).stem
            if fid in MEDIA:
                data, mime = MEDIA[fid]
                src = f"data:{mime};base64,{base64.b64encode(data).decode()}"
            elif img.url.startswith(("http://", "https://", "data:")):
                src = img.url
            else:
                continue
            cap = f"<figcaption>{html.escape(img.caption)}</figcaption>" if img.caption else ""
            w = f' style="width:{p.width}px"' if getattr(p, "width", 0) and p.width > 0 else ""
            out.append(
                f'<figure class="img"><img src="{src}" alt="{html.escape(img.caption)}"{w}>{cap}</figure>'
            )
        return "".join(out)


WIDGETS = {
    "selectbox", "radio", "multiselect", "slider", "select_slider", "toggle", "checkbox",
    "number_input", "date_input", "text_input", "text_area", "time_input", "color_picker",
    "segmented_control", "pills", "button_group",
}  # fmt: skip


# ------------------------------------------------------------------ dynamic sections
# A sub-tab whose controls keep working on the static page. "server" widgets are re-run with
# the real code for every combination (pre-rendered variants, fetched on first change);
# "client" widgets only show/hide chart traces in the browser, so every variant is rendered
# with them fully on ("preset") and the page hides what the viewer turned off.


@dataclass
class Dynamic:
    id: str
    path: tuple[str, ...]
    server: list[str]
    preset: dict[str, object]
    client: list[dict]  # {"key", "kind": "multi"|"toggle", "match": {opt: [regex]} | [regex]}
    fix_if: list[tuple[str, object, list[str]]] = field(default_factory=list)
    options: dict[str, list] = field(default_factory=dict)
    defaults: dict[str, object] = field(default_factory=dict)
    client_defaults: dict[str, object] = field(default_factory=dict)

    @property
    def live_keys(self) -> set[str]:
        return set(self.server) | {r["key"] for r in self.client}

    def initial(self, key: str, rendered):
        return self.client_defaults.get(key, rendered)

    def normalize(self, combo: dict) -> dict:
        """Collapse combinations whose output is identical (e.g. the window in Deltas mode)."""
        c = dict(combo)
        for k, v, reset in self.fix_if:
            if c[k] == v:
                for r in reset:
                    c[r] = self.defaults[r]
        return c

    def key(self, combo: dict) -> str:
        c = self.normalize(combo)
        return json.dumps([c[k] for k in self.server], ensure_ascii=False, separators=(",", ":"))

    def meta(self) -> dict:
        return {
            "src": f"dyn-{self.id}.json",
            "order": self.server,
            "defaults": self.defaults,
            "fix_if": self.fix_if,
            "client": self.client,
        }


_HORIZONS = ("1D", "1W", "1M", "3M", "1Y")
DYNAMIC_LIST = [
    Dynamic(
        id="cr_snapshot",
        path=("Curves & Rates", "Curve Snapshot"),
        server=["cr_series", "cr_mode", "cr_window", "cr_scale", "cr_grid"],
        preset={"cr_fit": True, "cr_model": True, "cr_h": list(_HORIZONS)},
        client=[
            {"key": "cr_h", "kind": "multi",
             "match": {h: [f"^{h} \\(", f"^Δ {h}$"] for h in _HORIZONS}},
            {"key": "cr_fit", "kind": "toggle", "match": ["^NSS fit$"]},
            {"key": "cr_model", "kind": "toggle", "match": ["^Model$"]},
        ],
        fix_if=[("cr_mode", "Deltas", ["cr_window"]), ("cr_grid", True, ["cr_series"])],
    ),
]  # fmt: skip
DYNAMIC = {d.path: d for d in DYNAMIC_LIST}
MAX_VARIANTS = 400


def _widget(tree, key: str):
    return next((n for n in _walk(tree) if getattr(n, "key", None) == key), None)


def _find_tab(tree, path: tuple[str, ...]):
    node = tree
    for label in path:
        node = next(
            (n for n in _walk(node) if getattr(n, "type", "") == "tab" and n.label == label), None
        )
        if node is None:
            return None
    return node


def prepare(d: Dynamic, tree) -> None:
    """Options and defaults from the base run (the page's initial state)."""
    for k in d.server:
        w = _widget(tree, k)
        if w is None:
            raise RuntimeError(f"dynamic section {d.id}: widget {k!r} not found")
        d.options[k] = [False, True] if w.type in ("toggle", "checkbox") else list(w.options)
        d.defaults[k] = w.value
    for r in d.client:
        w = _widget(tree, r["key"])
        d.client_defaults[r["key"]] = w.value if w is not None else None


def run_variants(at, d: Dynamic, r: Renderer) -> dict[str, str]:
    import itertools

    combos = {}
    for vals in itertools.product(*(d.options[k] for k in d.server)):
        c = d.normalize(dict(zip(d.server, vals, strict=True)))
        combos[d.key(c)] = c
    if len(combos) > MAX_VARIANTS:
        raise RuntimeError(f"{d.id}: {len(combos)} variants > {MAX_VARIANTS}")
    out = {}
    r.placeholders, r.dyn = False, d
    try:
        for key, c in combos.items():
            for k, v in {**c, **d.preset}.items():
                at.session_state[k] = v
            at.run()
            if at.exception:
                raise RuntimeError(f"{d.id} {key}: {at.exception[0].value}")
            node = _find_tab(at._tree, d.path)
            out[key] = r.children(node)
    finally:
        r.placeholders, r.dyn = True, None
    return out


def dyn_wrapper(d: Dynamic, html_: str) -> str:
    state = {
        "server": {k: d.defaults[k] for k in d.server},
        "client": d.client_defaults,
    }
    meta = html.escape(json.dumps(d.meta(), ensure_ascii=False), quote=True)
    st_ = html.escape(json.dumps(state, ensure_ascii=False), quote=True)
    return f'<div class="dyn" data-meta="{meta}" data-state="{st_}">{html_}</div>'


# ------------------------------------------------------------------ page

CSS = """
:root{--bg:#F5F7FB;--panel:#FFFFFF;--soft:#E9EFF9;--ink:#14213D;--muted:#5B6478;--line:#D5DDEC;
--blue:#0039A6;--red:#D52B1E;--radius:.6rem}
*{box-sizing:border-box}
body{margin:0;background:var(--bg) radial-gradient(1200px 500px at 85% -10%,rgba(0,57,166,.08),transparent 60%) no-repeat;
color:var(--ink);font:15px/1.5 Inter,system-ui,-apple-system,Segoe UI,sans-serif}
a{color:var(--blue)}
.wrap{max-width:1500px;margin:0 auto;padding:16px 24px 48px}
.cm-header{display:flex;gap:14px;align-items:center;margin:4px 0 10px}
.cm-flag-link img{height:40px;width:auto;border-radius:6px;box-shadow:0 2px 8px rgba(20,33,61,.18);display:block;transition:transform .2s}
.cm-flag-link:hover img{transform:rotate(-4deg) scale(1.06)}
.cm-title{font:700 30px/1.1 'Space Grotesk',Inter,sans-serif;margin:0;letter-spacing:-.01em}
.cm-title span{color:var(--blue)}
.cm-sub{color:var(--muted);font-size:13px}
.cm-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#1a7f37;margin-right:6px;vertical-align:1px}
nav.top{display:flex;flex-wrap:wrap;gap:2px;border-bottom:1px solid var(--line);margin:8px 0 18px}
nav.top a{padding:9px 14px;text-decoration:none;color:var(--muted);font-weight:500;border-bottom:3px solid transparent;margin-bottom:-1px}
nav.top a:hover{color:var(--ink)}
nav.top a[aria-current]{color:var(--blue);border-bottom-color:var(--red);font-weight:600}
.tablist{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 16px}
.tablist button{font:inherit;font-size:14px;border:1px solid var(--line);background:var(--panel);color:var(--ink);
padding:6px 14px;border-radius:999px;cursor:pointer;transition:background .15s,border-color .15s}
.tablist button:hover{border-color:var(--blue)}
.tablist button[aria-selected=true]{background:var(--blue);border-color:var(--blue);color:#fff}
.tabs .tabs .tablist button{font-size:13px;padding:4px 12px}
.cols{display:grid;gap:16px;align-items:start}
@media (max-width:900px){.cols{grid-template-columns:1fr!important}}
.col{min-width:0}
h2,h3,h4,h5{font-family:'Space Grotesk',Inter,sans-serif;margin:18px 0 8px}
.md p{margin:6px 0}
.caption{color:var(--muted);font-size:13px;margin:4px 0 10px}
.caption p{margin:2px 0}
.metric{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px;margin:0 0 10px;transition:box-shadow .15s,transform .15s}
.metric:hover{box-shadow:0 4px 16px rgba(20,33,61,.08);transform:translateY(-1px)}
.metric .label{color:var(--muted);font-size:13px}
.metric .value{font:600 26px/1.2 'Space Grotesk',Inter,sans-serif;margin-top:2px}
.delta{font-size:13px;margin-top:2px}.delta.pos{color:#1a7f37}.delta.neg{color:#cf222e}.delta.off{color:var(--muted)}
.plot{width:100%;min-height:320px;background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);margin:6px 0 12px;padding:4px}
.tbl{max-height:440px;overflow:auto;border:1px solid var(--line);border-radius:var(--radius);background:var(--panel);margin:6px 0 12px}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th{position:sticky;top:0;background:var(--soft);text-align:left;font-weight:600;white-space:nowrap}
th,td{padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tbody tr:hover td{background:#F0F4FB}
.chip{display:inline-block;font-size:12px;background:var(--soft);border:1px solid var(--line);border-radius:999px;padding:2px 10px;margin:2px 6px 6px 0;color:var(--muted)}
.chip b{color:var(--ink);font-weight:600;margin-right:4px}
.alert{border-radius:var(--radius);padding:10px 14px;margin:8px 0;border:1px solid}
.alert p{margin:2px 0}
.alert.info{background:#EAF1FF;border-color:#BFD2F7}.alert.warning{background:#FFF8E5;border-color:#F0DC9C}
.alert.success{background:#EAF7EE;border-color:#B7E0C3}.alert.error{background:#FDECEC;border-color:#F3B9B9}
details.exp{border:1px solid var(--line);border-radius:var(--radius);background:var(--panel);padding:8px 14px;margin:8px 0}
details.exp summary{cursor:pointer;font-weight:600}
.progress{height:8px;background:var(--soft);border-radius:4px;overflow:hidden;margin:8px 0}.progress span{display:block;height:100%;background:var(--blue)}
figure.img{margin:6px 0}figure.img img{max-width:100%;border-radius:8px}figure.img figcaption{font-size:12px;color:var(--muted)}
code{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:.88em;background:var(--soft);padding:1px 5px;border-radius:4px}
hr{border:0;border-top:1px solid var(--line);margin:16px 0}
.muted{color:var(--muted)}
.ctl{display:inline-flex;align-items:center;gap:8px;margin:2px 14px 10px 0;font-size:13px;color:var(--muted);flex-wrap:wrap}
.ctl>span{font-weight:600;color:var(--ink)}
.ctl select{font:inherit;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:5px 28px 5px 10px;cursor:pointer}
.ctl select:hover,.ctl select:focus{border-color:var(--blue);outline:none}
.seg{display:inline-flex;flex-wrap:wrap;gap:4px}
.seg button{font:inherit;font-size:12.5px;border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:4px 11px;border-radius:999px;cursor:pointer;transition:background .15s,border-color .15s}
.seg button:hover{border-color:var(--blue)}
.seg button[aria-pressed=true]{background:var(--blue);border-color:var(--blue);color:#fff}
.ctl.tog{cursor:pointer;color:var(--ink);font-weight:500}
.ctl.tog input{position:absolute;opacity:0;pointer-events:none}
.sw{width:32px;height:18px;border-radius:999px;background:#C5CEDF;position:relative;transition:background .15s;flex:none}
.sw::after{content:"";position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;background:#fff;transition:transform .15s;box-shadow:0 1px 2px rgba(0,0,0,.2)}
.tog input:checked+.sw{background:var(--blue)}.tog input:checked+.sw::after{transform:translateX(14px)}
.tog input:focus-visible+.sw{outline:2px solid var(--blue);outline-offset:2px}
.ctl :disabled,.ctl.tog:has(input:disabled){opacity:.45;cursor:not-allowed}
.dyn.loading{opacity:.55;transition:opacity .1s}
footer{margin-top:32px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:12px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

JS = """
const LINKS = %s;
document.querySelectorAll('.cm-flag-link').forEach(a => a.addEventListener('click', e => {
  e.preventDefault(); window.open(LINKS[Math.floor(Math.random() * LINKS.length)], '_blank', 'noopener');
}));
const FONT = {family: 'Inter, system-ui, sans-serif', color: '#14213D'};
function drawVisible(root) {
  root.querySelectorAll('.plot').forEach(div => {
    if (div.dataset.done || div.offsetParent === null) return;
    const src = document.querySelector('script[data-for="' + div.id + '"]');
    const fig = JSON.parse(src.textContent);
    const layout = Object.assign({}, fig.layout || {});
    delete layout.width; layout.autosize = true;
    layout.paper_bgcolor = layout.paper_bgcolor || 'rgba(0,0,0,0)';
    layout.plot_bgcolor = layout.plot_bgcolor || 'rgba(0,0,0,0)';
    layout.font = Object.assign({}, FONT, layout.font || {});
    // Streamlit's chart theme grows the (tight) margins to fit tick labels; do the same here
    const tpl = Object.assign({}, layout.template || {});
    tpl.layout = Object.assign({}, tpl.layout || {}, {xaxis: {automargin: true}, yaxis: {automargin: true}});
    layout.template = tpl;
    Object.keys(layout).filter(k => /^[xy]axis\\d*$/.test(k)).forEach(k => layout[k].automargin = true);
    const dyn = div.closest('.dyn');
    const data = (fig.data || []).map(t => dyn ? Object.assign({}, t, {visible: traceVisible(dyn, t.name)}) : t);
    Plotly.newPlot(div, data, layout, {responsive: true, displaylogo: false});
    div.dataset.done = '1';
  });
}
document.querySelectorAll('.tablist').forEach(list => {
  const tabs = [...list.children];
  const panels = [...list.parentElement.children].filter(c => c.classList.contains('panel'));
  tabs.forEach((b, i) => b.addEventListener('click', () => {
    tabs.forEach((t, j) => t.setAttribute('aria-selected', String(i === j)));
    panels.forEach((p, j) => p.hidden = i !== j);
    drawVisible(panels[i]);
  }));
});

// ---- dynamic sections: pre-rendered variants (server controls) + trace toggles (client)
const META = el => JSON.parse(el.dataset.meta);
const variantCache = {};
function traceVisible(dyn, name) {
  const st = dyn._state || JSON.parse(dyn.dataset.state);
  for (const rule of META(dyn).client) {
    const v = st.client[rule.key];
    if (rule.kind === 'toggle') {
      if (!v && rule.match.some(rx => new RegExp(rx).test(name || ''))) return false;
    } else {
      for (const [opt, rxs] of Object.entries(rule.match))
        if (!(v || []).includes(opt) && rxs.some(rx => new RegExp(rx).test(name || ''))) return false;
    }
  }
  return true;
}
function applyClient(dyn) {
  dyn.querySelectorAll('.plot').forEach(div => {
    if (!div.dataset.done || !div.data) return;
    Plotly.restyle(div, {visible: div.data.map(t => traceVisible(dyn, t.name))});
  });
}
function readCtl(c) {
  if (c.tagName === 'SELECT') return JSON.parse(c.value);
  if (c.type === 'checkbox') return c.checked;
  const on = [...c.querySelectorAll('button[aria-pressed=true]')].map(b => JSON.parse(b.dataset.v));
  return c.dataset.kind === 'multi' ? on : on[0];
}
function writeCtl(c, v) {
  if (c.tagName === 'SELECT') { c.value = JSON.stringify(v); return; }
  if (c.type === 'checkbox') { c.checked = !!v; return; }
  const set = c.dataset.kind === 'multi' ? (v || []).map(x => JSON.stringify(x)) : [JSON.stringify(v)];
  c.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(set.includes(b.dataset.v))));
}
function variantKey(meta, server) {
  const c = Object.assign({}, server);
  for (const [k, v, reset] of meta.fix_if) if (JSON.stringify(c[k]) === JSON.stringify(v)) reset.forEach(r => c[r] = meta.defaults[r]);
  return JSON.stringify(meta.order.map(k => c[k]));
}
async function swap(dyn) {
  const meta = META(dyn);
  dyn.classList.add('loading');
  try {
    variantCache[meta.src] = variantCache[meta.src] || fetch(meta.src).then(r => r.json());
    const html = (await variantCache[meta.src])[variantKey(meta, dyn._state.server)];
    if (html === undefined) throw new Error('no pre-rendered view for this combination');
    dyn.innerHTML = html;
    bindDyn(dyn);
    drawVisible(dyn);
  } catch (e) {
    alert('Could not load this view: ' + e.message);
  } finally { dyn.classList.remove('loading'); }
}
function bindDyn(dyn) {
  dyn._state = dyn._state || JSON.parse(dyn.dataset.state);
  dyn.querySelectorAll('[data-k]').forEach(c => {
    const k = c.dataset.k, role = c.dataset.role;
    writeCtl(c, dyn._state[role][k]);
    const changed = () => {
      dyn._state[role][k] = readCtl(c);
      if (role === 'server') swap(dyn); else applyClient(dyn);
    };
    if (c.tagName === 'SELECT' || c.type === 'checkbox') c.addEventListener('change', changed);
    else c.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
      if (b.disabled) return;
      if (c.dataset.kind === 'multi') b.setAttribute('aria-pressed', String(b.getAttribute('aria-pressed') !== 'true'));
      else c.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
      changed();
    }));
  });
}
document.querySelectorAll('.dyn').forEach(bindDyn);
drawVisible(document);
"""


def slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def page(title: str, nav: str, body: str, built: datetime) -> str:
    import style

    plotly_js = (
        f"https://cdn.jsdelivr.net/npm/plotly.js-dist-min@{get_plotlyjs_version()}/plotly.min.js"
    )
    fonts = (
        "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700"
        "&family=Space+Grotesk:wght@500;600;700&family=JetBrains+Mono:wght@400;600&display=swap"
    )
    flag = f'<a class="cm-flag-link" href="#" title="¡Viva Chile!">{style.FLAG_IMG}</a>'
    scl = built.astimezone(SCL)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Chile Monitor · {html.escape(title)}</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🇨🇱</text></svg>">
<link rel="stylesheet" href="{fonts}">
<style>{CSS}</style>
<script src="{plotly_js}"></script>
</head><body><div class="wrap">
<div class="cm-header">{flag}<div><h1 class="cm-title">Chile <span>Monitor</span></h1>
<div class="cm-sub"><span class="cm-dot"></span>Public data only · data as of {built.astimezone(NY):%a %d %b %Y %H:%M} NY · {scl:%H:%M} Santiago</div></div></div>
<nav class="top">{nav}</nav>
{body}
<footer>Static snapshot built by the morning job from the Chile Monitor code. Charts are interactive;
filters show their default settings and Refresh buttons are omitted. Sources and as-of dates are
under each chart and table.</footer>
</div><script>{JS % json.dumps(style.FLAG_LINKS)}</script></body></html>"""


def build(out: Path, timeout: float = 900) -> dict[str, object]:
    from streamlit.testing.v1 import AppTest

    _capture_media()
    t0 = time.monotonic()
    at = AppTest.from_file(str(APP / "main.py"), default_timeout=timeout)
    at.run()
    errors = [str(e.value)[:300] for e in at.exception]
    top = next(n for n in _walk(at._tree) if getattr(n, "type", "") == "tab_container")
    tabs = [k for k in top.children.values() if getattr(k, "type", "") == "tab"]
    files = ["index.html"] + [f"{slug(k.label)}.html" for k in tabs[1:]]
    built = datetime.now(NY)
    out.mkdir(parents=True, exist_ok=True)
    r = Renderer()
    for d in DYNAMIC_LIST:
        prepare(d, at._tree)
    docs = {}
    for i, (k, fname) in enumerate(zip(tabs, files, strict=True)):
        nav = "".join(
            f'<a href="{f}"{" aria-current=page" if j == i else ""}>{html.escape(t.label)}</a>'
            for j, (t, f) in enumerate(zip(tabs, files, strict=True))
        )
        r.path = [k.label]
        docs[fname] = page(k.label, nav, r.children(k), built)
        r.path = []
    n_variants = 0
    for d in DYNAMIC_LIST:  # reruns change the app state: after every page is rendered
        variants = run_variants(at, d, r)
        n_variants += len(variants)
        (out / f"dyn-{d.id}.json").write_text(
            json.dumps(variants, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        default = variants[d.key(d.defaults)]
        for fname, doc in docs.items():
            docs[fname] = doc.replace(f"<!--DYN:{d.id}-->", dyn_wrapper(d, default))
    for fname, doc in docs.items():
        (out / fname).write_text(doc, encoding="utf-8")
    (out / ".nojekyll").write_text("")
    return {
        "pages": len(files),
        "plots": r.n_plots,
        "variants": n_variants,
        "bytes": sum(len(d) for d in docs.values()),
        "app_exceptions": errors,
        "unknown_elements": r.unknown,
        "seconds": round(time.monotonic() - t0, 1),
    }


def _walk(n):
    yield n
    for c in getattr(n, "children", {}).values():
        yield from _walk(c)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="site")
    a = p.parse_args()
    res = build(Path(a.out))
    print(json.dumps(res))
    return 1 if res["app_exceptions"] else 0


if __name__ == "__main__":
    sys.exit(main())
