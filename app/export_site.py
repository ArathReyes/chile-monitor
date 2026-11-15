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
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from plotly.offline import get_plotlyjs_version

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
import site_dynamic as sd  # noqa: E402

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


def _tolerant_replay() -> None:
    """AppTest replays each selectbox/radio value against the *previous* run's options. When a
    filter removes the selected option (a bond outside the chosen types), that replay raises
    before the app runs, and every later run fails too. Skip such a value instead: the value
    the build sets in session_state then applies."""
    from streamlit.testing.v1 import element_tree as et

    for cls in (et.Selectbox, et.Radio):
        orig = cls.index

        def index(self, _orig=orig):
            try:
                return _orig.fget(self)
            except ValueError:
                return None

        cls.index = property(index)


def _no_cache_expiry() -> None:
    """The build re-runs the app hundreds of times over several minutes; the app's cache TTLs
    (1-5 min, meant for a live session) would expire mid-build and reload everything on every
    re-run. One build = one snapshot, so keep cached data for the whole build."""
    import streamlit as st

    for name in ("cache_data", "cache_resource"):
        orig = getattr(st, name)

        class NoTTL:
            def __init__(self, orig):
                self._orig = orig

            def __call__(self, func=None, **kw):
                kw.pop("ttl", None)
                return self._orig(func, **kw) if func is not None else self._orig(**kw)

            def __getattr__(self, attr):
                return getattr(self._orig, attr)

        setattr(st, name, NoTTL(orig))


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
        self.sections: dict[tuple[str, ...], sd.Section] = {}  # sub-tabs with working controls
        self.section: sd.Section | None = None  # set while pre-rendering one: widgets go live
        self.hashing = False  # True while measuring dependencies (ids/controls left out)

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
        if self.section is not None and key in self.section.controls:
            return "" if self.hashing else self.live(n, self.section.controls[key])
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

    def live(self, n, c: sd.Control) -> str:
        """A working control (see site_dynamic): the value it shows is synced by the page."""
        label = _inline(c.label, False)
        dis = " disabled" if getattr(n.proto, "disabled", False) else ""
        attrs = f'data-k="{html.escape(c.key)}" data-role="{c.role}"'
        if c.type in ("toggle", "checkbox"):
            chk = " checked" if n.value else ""
            return (
                f'<label class="ctl tog"><input type="checkbox" {attrs}{chk}{dis}>'
                f'<span class="sw"></span>{label}</label>'
            )
        if c.type == "multiselect":  # pills of the base options; the value is the pressed list
            opts, labels, kind = c.base, c.base_labels, "multi"
            cur = {sd.jkey([o]) for o in (n.value or [])}
        else:
            opts, labels, kind = c.options, c.labels, "one"
            cur = {sd.jkey([n.value if c.type != "date_input" else tuple(n.value)])}
        hint = '<em class="hint">none = all</em>' if c.role == "client" and kind == "multi" else ""
        if c.type == "selectbox" or (kind == "one" and len(opts) > 9):
            options = "".join(
                f'<option value="{sd.jattr(o)}"{" selected" if sd.jkey([o]) in cur else ""}>'
                f"{html.escape(lab)}</option>"
                for o, lab in zip(opts, labels, strict=True)
            )
            return f'<label class="ctl"><span>{label}</span><select {attrs}{dis}>{options}</select></label>'
        btns = "".join(
            f'<button type="button" data-v="{sd.jattr(o)}" '
            f'aria-pressed="{str(sd.jkey([o]) in cur).lower()}"{dis}>{html.escape(lab)}</button>'
            for o, lab in zip(opts, labels, strict=True)
        )
        many = " many" if len(opts) > 12 else ""
        return (
            f'<div class="ctl"><span>{label}</span>{hint}'
            f'<div class="seg{many}" {attrs} data-kind="{kind}">{btns}</div></div>'
        )

    def hashes(self, tab, s: sd.Section) -> list[str]:
        self.hashing, self.section = True, s
        try:
            return [sd.digest(self.node(b)) for b in sd.blocks(tab)]
        finally:
            self.hashing, self.section = False, None

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
            sec = self.sections.get(tuple(self.path))
            if sec is not None and self.section is None:
                return f"<!--DYN:{sec.id}-->"
            return self.children(tab)
        finally:
            self.path.pop()

    def plot(self, p) -> str:
        if self.hashing:
            return sd.digest(p.spec)
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
.seg.many{max-height:92px;overflow:auto;padding:2px;border:1px solid var(--line);border-radius:10px;background:var(--panel);max-width:640px}
.ctl .hint{font-size:11.5px;color:var(--muted)}
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

%s
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
</div><script>{JS % (json.dumps(style.FLAG_LINKS), sd.JS)}</script></body></html>"""


def build(out: Path, timeout: float = 900) -> dict[str, object]:
    from streamlit.testing.v1 import AppTest

    _capture_media()
    _no_cache_expiry()
    _tolerant_replay()
    t0 = time.monotonic()
    at = AppTest.from_file(str(APP / "main.py"), default_timeout=timeout)
    at.run()
    print(f"base run {time.monotonic() - t0:.1f}s", file=sys.stderr, flush=True)
    errors = [str(e.value)[:300] for e in at.exception]
    top = next(n for n in _walk(at._tree) if getattr(n, "type", "") == "tab_container")
    tabs = [k for k in top.children.values() if getattr(k, "type", "") == "tab"]
    files = ["index.html"] + [f"{slug(k.label)}.html" for k in tabs[1:]]
    built = datetime.now(NY)
    out.mkdir(parents=True, exist_ok=True)
    r = Renderer()
    r.sections = sd.find_sections(at._tree, at)
    docs = {}
    for i, (k, fname) in enumerate(zip(tabs, files, strict=True)):
        nav = "".join(
            f'<a href="{f}"{" aria-current=page" if j == i else ""}>{html.escape(t.label)}</a>'
            for j, (t, f) in enumerate(zip(tabs, files, strict=True))
        )
        r.path = [k.label]
        docs[fname] = page(k.label, nav, r.children(k), built)
        r.path = []
    report = {}
    for sec in r.sections.values():  # reruns change the app state: after every page is rendered
        t1 = time.monotonic()
        runner = sd.Runner(at, sec)
        sd.analyse(sec, runner, r)
        sd.render(sec, runner, r)
        if sec.variants:
            (out / f"dyn-{sec.id}.json").write_text(sd.variants_json(sec), encoding="utf-8")
        for fname, doc in docs.items():
            docs[fname] = doc.replace(f"<!--DYN:{sec.id}-->", sd.wrapper(sec))
        report[sec.id] = {
            "units": [[u[0], d] for u, d in zip(sec.units, sec.deps, strict=True) if d],
            "variants": sum(len(v) for v in sec.variants.values()),
            "skipped": sec.skipped,
            "s": round(time.monotonic() - t1, 1),
        }
        print(sec.id, report[sec.id], file=sys.stderr, flush=True)
    for fname, doc in docs.items():
        (out / fname).write_text(doc, encoding="utf-8")
    (out / ".nojekyll").write_text("")
    return {
        "pages": len(files),
        "plots": r.n_plots,
        "variants": sum(v["variants"] for v in report.values()),
        "sections": report,
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
