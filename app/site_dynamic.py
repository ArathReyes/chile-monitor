"""Working controls on the static site.

A static page can't re-run Python, so every sub-tab with controls is pre-rendered by the real
code for each value its controls can take, and the browser swaps the pieces in:

1. **Which piece depends on which control** is measured, not declared: the sub-tab is re-run
   with one control changed at a time and every top-level block whose output changes is marked
   as depending on it. Each block is then pre-rendered only over the controls it depends on
   (the statement viewer depends on "Meeting", not on the charts' window).
   If a control changes the *structure* (e.g. Grid shows 7 charts instead of 1), the stretch
   between the unchanged head and tail is one unit.
2. **"Server" controls** (dropdowns, radios, toggles, sliders/numbers/dates on a grid of values
   from VALUES / DATE_RANGES) select among the pre-rendered blocks, fetched from
   `dyn-<sub-tab>.json` the first time a control is used.
3. **"Client" controls** (CLIENT) act in the browser: show/hide chart traces by name, or filter
   table rows by a column. Pre-rendering uses their PRESET value (everything shown).
4. Free-typed inputs (text, numbers and dates without a grid) stay fixed and show their value.

Values are matched by canonical JSON (`jv`), identical in Python and JavaScript.
"""

from __future__ import annotations

import hashlib
import html
import itertools
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

log = logging.getLogger(__name__)

HORIZONS = ("1D", "1W", "1M", "3M", "1Y")

# Values offered for continuous controls (must include the app's default).
VALUES: dict[str, list] = {
    "mp_lam": [0.0, 0.005, 0.02, 0.05, 0.2],
    "mp_mu": [0.0, 0.02, 0.05, 0.1, 0.5, 2.0],
    "cm_season_window": [5, 8, 10, 12, 15, 20, 25],
    "cm_hl": [1.0, 2.0, 3.0, 4.0, 6.0, 9.0, 12.0],
    "cm_si_years": [2, 3, 4, 6, 8, 10, 15, 20],
    "bm_conv_face": [100.0, 500.0, 1000.0, 5000.0, 10000.0],
}
# Date-range inputs: (label, days back from today or None = from the earliest date).
DATE_RANGES: dict[str, list[tuple[str, int | None]]] = {
    "bm_r_d": [("Last 1Y", 365), ("Last 2Y", 730), ("Last 5Y", 1826), ("All", None)],
}
# Browser-side controls.
CLIENT: dict[str, dict] = {
    "cr_h": {
        "kind": "traces",
        "match": {h: [f"^{h} \\(", f"^Δ {h}$"] for h in HORIZONS},
        "preset": list(HORIZONS),
    },
    "cr_fit": {"kind": "traces", "match": ["^NSS fit$"], "preset": True},
    "cr_model": {"kind": "traces", "match": ["^Model$"], "preset": True},
    "bm_r_n": {"kind": "rows", "column": "Nemo", "preset": []},
}
# Combinations with identical output: when key == value, the listed keys are irrelevant.
FIX_IF: list[tuple[str, object, list[str]]] = [
    ("cr_mode", "Deltas", ["cr_window"]),
    ("cr_grid", True, ["cr_series"]),
]
MAX_OPTIONS = 60  # a dropdown with more options stays fixed
MAX_MULTI = 4  # multiselects up to this many options are enumerated (2^n subsets)
MAX_COMBOS = 400  # per block
PROBES = 3  # alternative values tried per control when measuring dependencies


def jv(v):
    """Canonical JSON-able value (dates → ISO, integral floats → int, tuples → lists)."""
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, list | tuple):
        return [jv(x) for x in v]
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def jkey(values: list) -> str:
    return json.dumps([jv(v) for v in values], ensure_ascii=False, separators=(",", ":"))


def jattr(v) -> str:
    return html.escape(json.dumps(jv(v), ensure_ascii=False, separators=(",", ":")), quote=True)


@dataclass
class Control:
    key: str
    type: str
    label: str
    options: list  # python values (set into session_state)
    labels: list[str]
    default: object
    role: str  # "server" | "client"
    rule: dict | None = None
    base: list | None = None  # multiselect: the individual options (real values) …
    base_labels: list | None = None  # … and how they are shown


def _proto_date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y/%m/%d").date()
    except (TypeError, ValueError):
        return None


def raw_options(at, n) -> list | None:
    """The real option values behind a widget's display labels (AppTest only exposes the
    formatted strings, e.g. "10y" for 10), from Streamlit's widget metadata."""
    try:
        meta = at.session_state._state._state._new_widget_state.widget_metadata[n.id]
        return list(meta.deserializer.__self__.options)
    except (AttributeError, KeyError, TypeError):
        return None


def control_for(n, today: date | None = None, raw: list | None = None) -> Control | None:
    """The control a widget becomes on the site, or None to keep it fixed. `raw` = the real
    option values (default: the display labels)."""
    t, k = n.type, getattr(n, "key", None)
    if not k or t == "button":
        return None
    label = n.label or ""
    v = n.value
    shown = list(getattr(n, "options", []) or [])
    base = raw if raw is not None and len(raw) == len(shown) else shown
    if k in CLIENT:
        rule = CLIENT[k]
        if t in ("toggle", "checkbox"):
            return Control(k, t, label, [False, True], ["off", "on"], v, "client", rule)
        return Control(k, t, label, base, [str(x) for x in shown], v, "client", rule, base, shown)
    if t in ("toggle", "checkbox"):
        return Control(k, t, label, [False, True], ["off", "on"], v, "server")
    if t in ("selectbox", "radio", "select_slider"):
        if not base or len(base) > MAX_OPTIONS:
            return None
        return Control(k, t, label, base, [str(x) for x in shown], v, "server")
    if t in ("slider", "number_input") and k in VALUES:
        cast = type(v) if isinstance(v, int | float) else float
        opts = [cast(x) for x in VALUES[k]]
        if jv(v) not in [jv(o) for o in opts]:
            log.warning("%s: default %r not in VALUES; kept fixed", k, v)
            return None
        return Control(k, t, label, opts, [f"{o:g}" for o in opts], v, "server")
    if t == "multiselect" and 0 < len(base) <= MAX_MULTI:
        subsets = [  # every non-empty selection (an empty filter shows nothing)
            [x for x, keep in zip(base, mask, strict=True) if keep]
            for mask in itertools.product([True, False], repeat=len(base))
            if any(mask)
        ]
        labels = [", ".join(map(str, x)) or "none" for x in subsets]
        return Control(k, t, label, subsets, labels, v, "server", None, base, shown)
    if t == "date_input" and k in DATE_RANGES and isinstance(v, tuple | list) and len(v) == 2:
        today = today or date.today()
        lo = _proto_date(n.proto.min) or (v[0] - timedelta(days=3650))
        end = v[1]
        opts, labels = [], []
        for lab, days in DATE_RANGES[k]:
            start = lo if days is None else max(lo, today - timedelta(days=days))
            opts.append((start, end))
            labels.append(lab)
        if jv(tuple(v)) not in [jv(o) for o in opts]:
            opts.insert(0, tuple(v))
            labels.insert(0, "Default")
        return Control(k, t, label, opts, labels, tuple(v), "server")
    return None


@dataclass
class Section:
    path: tuple[str, ...]
    controls: dict[str, Control]
    units: list[tuple] = field(default_factory=list)  # see _unit_html
    deps: list[list[str]] = field(default_factory=list)  # per unit
    variants: dict[int, dict[str, str]] = field(default_factory=dict)
    initial: list[str] = field(default_factory=list)  # html per block (default state)
    skipped: int = 0

    @property
    def id(self) -> str:
        import re

        return re.sub(r"[^a-z0-9]+", "-", " ".join(self.path).lower()).strip("-")

    @property
    def server(self) -> list[str]:
        return [k for k, c in self.controls.items() if c.role == "server"]

    def fix_if(self) -> list:
        return [
            (k, v, [r for r in rs if r in self.controls])
            for k, v, rs in FIX_IF
            if k in self.controls
        ]

    def normalize(self, combo: dict) -> dict:
        c = dict(combo)
        for k, v, rs in self.fix_if():
            if jv(c.get(k)) == jv(v):
                for r in rs:
                    c[r] = self.controls[r].default
        return c

    def key(self, dims: list[str], combo: dict) -> str:
        c = self.normalize(combo)
        return jkey([c[d] for d in dims])

    def meta(self) -> dict:
        return {
            "src": f"dyn-{self.id}.json",
            "controls": {k: ({"role": c.role} | (c.rule or {})) for k, c in self.controls.items()},
            "units": {str(i): d for i, d in enumerate(self.deps) if d},
            "defaults": {k: jv(c.default) for k, c in self.controls.items()},
            "fix_if": [[k, jv(v), rs] for k, v, rs in self.fix_if()],
        }

    def state(self) -> dict:
        return {
            "server": {k: jv(c.default) for k, c in self.controls.items() if c.role == "server"},
            "client": {k: jv(c.default) for k, c in self.controls.items() if c.role == "client"},
        }


# ------------------------------------------------------------------ tree helpers


def walk(n):
    yield n
    for c in getattr(n, "children", {}).values():
        yield from walk(c)


def find_tab(tree, path: tuple[str, ...]):
    node = tree
    for label in path:
        node = next(
            (x for x in walk(node) if getattr(x, "type", "") == "tab" and x.label == label), None
        )
        if node is None:
            return None
    return node


def leaf_tabs(tree, path: tuple[str, ...] = ()):
    """(path, tab) for every tab without nested tabs."""
    for n in getattr(tree, "children", {}).values():
        if getattr(n, "type", "") == "tab":
            p = (*path, n.label)
            inner = list(leaf_tabs(n, p))
            if inner:
                yield from inner
            else:
                yield p, n
        else:
            yield from leaf_tabs(n, path)


def blocks(tab) -> list:
    return list(getattr(tab, "children", {}).values())


# ------------------------------------------------------------------ analysis and rendering


def find_sections(tree, at=None) -> dict[tuple[str, ...], Section]:
    out = {}
    for path, tab in leaf_tabs(tree):
        controls = {}
        for n in walk(tab):
            if not (hasattr(n, "key") and hasattr(n, "label") and hasattr(n, "id")):
                continue
            c = control_for(n, raw=raw_options(at, n) if at is not None else None)
            if c is not None:
                controls[c.key] = c
        if controls:
            out[path] = Section(path, controls)
    return out


class Runner:
    """Re-runs the app with a section's controls set; returns the section's tab node."""

    TIMEOUT = 120  # s per re-run; a combination that takes longer is skipped

    def __init__(self, at, section: Section) -> None:
        self.at, self.s = at, section
        self.n, self.secs = 0, 0.0

    def run(self, combo: dict | None = None):
        t0 = time.monotonic()
        try:
            return self._run(combo)
        finally:
            dt = time.monotonic() - t0
            self.n += 1
            self.secs += dt
            if dt > 10 or self.n % 25 == 0:
                print(
                    f"  {self.s.id} run {self.n}: {dt:.1f}s (avg {self.secs / self.n:.1f}s)",
                    file=sys.stderr,
                    flush=True,
                )

    def _run(self, combo: dict | None = None):
        combo = combo or {}
        for k, c in self.s.controls.items():
            v = c.rule["preset"] if c.role == "client" else combo.get(k, c.default)
            self.at.session_state[k] = list(v) if isinstance(v, list) else v
        try:
            self.at.run(timeout=self.TIMEOUT)
            err = self.at.exception[0].value if self.at.exception else None
        except Exception as e:  # noqa: BLE001
            err = e
        if err is None:
            return find_tab(self.at._tree, self.s.path)
        log.warning("%s %s: %s", self.s.id, combo, err)
        self._recover()
        return None

    def _recover(self) -> None:
        """A failed run leaves a truncated widget tree that would pin later runs' values:
        re-run once from the defaults so the next combination starts clean."""
        for k, c in self.s.controls.items():
            v = c.rule["preset"] if c.role == "client" else c.default
            self.at.session_state[k] = list(v) if isinstance(v, list) else v
        try:
            self.at.run(timeout=self.TIMEOUT)
        except Exception as e:  # noqa: BLE001
            log.warning("%s: recovery run failed: %s", self.s.id, e)

    def reset(self) -> None:
        for k, c in self.s.controls.items():
            self.at.session_state[k] = c.default


def _probes(c: Control) -> list:
    alts = [o for o in c.options if jv(o) != jv(c.default)]
    if len(alts) <= PROBES:
        return alts
    step = (len(alts) - 1) / (PROBES - 1)
    return [alts[round(i * step)] for i in range(PROBES)]


def _unit_html(r, bs: list, unit: tuple) -> str:
    """Render one unit of a (possibly re-structured) block list: ("s", i) = i-th block from
    the start, ("e", j) = j-th from the end, ("r", p, q) = everything between p blocks from
    the start and q from the end."""
    if unit[0] == "s":
        return r.node(bs[unit[1]]) if unit[1] < len(bs) else ""
    if unit[0] == "e":
        j = len(bs) - 1 - unit[1]
        return r.node(bs[j]) if j >= 0 else ""
    _, p, q = unit
    return "".join(r.node(b) for b in bs[p : len(bs) - q])


def analyse(s: Section, runner: Runner, r) -> None:
    """Measure which top-level block depends on which server control. A control that changes
    the number of blocks marks the stretch between the unchanged head and tail as one unit."""
    tab = runner.run()
    if tab is None:
        raise RuntimeError(f"{s.id}: default state failed")
    h0 = r.hashes(tab, s)
    n0 = len(h0)
    deps: list[set[str]] = [set() for _ in h0]
    region: list[int] | None = None  # [p, q]
    region_deps: set[str] = set()
    for k in s.server:
        for v in _probes(s.controls[k]):
            t = runner.run({k: v})
            if t is None:
                continue
            h = r.hashes(t, s)
            if len(h) == n0:
                for i, (a, b) in enumerate(zip(h0, h, strict=True)):
                    if a != b:
                        deps[i].add(k)
                continue
            m = min(len(h), n0)
            p = 0
            while p < m and h[p] == h0[p]:
                p += 1
            q = 0
            while q < m - p and h[-1 - q] == h0[-1 - q]:
                q += 1
            region = [p, q] if region is None else [min(region[0], p), min(region[1], q)]
            region_deps.add(k)
    order = lambda ks: [k for k in s.server if k in ks]  # noqa: E731
    if region is None:
        s.units = [("s", i) for i in range(n0)]
        s.deps = [order(d) for d in deps]
        return
    p, q = region
    end = max(p, n0 - q)
    s.units = [("s", i) for i in range(p)] + [("r", p, q)]
    s.units += [("e", n0 - 1 - i) for i in range(end, n0)]
    s.deps = [order(deps[i]) for i in range(p)]
    s.deps.append(order(region_deps.union(*deps[p:end])))
    s.deps += [order(deps[i]) for i in range(end, n0)]


def render(s: Section, runner: Runner, r) -> None:
    """Pre-render every unit over the controls it depends on."""
    tab = runner.run()
    r.section = s
    try:
        bs0 = blocks(tab)
        s.initial = [_unit_html(r, bs0, u) for u in s.units]
        groups: dict[tuple[str, ...], list[int]] = {}
        for i, d in enumerate(s.deps):
            if d:
                groups.setdefault(tuple(d), []).append(i)
        for dims, idxs in groups.items():
            combos: dict[str, dict] = {}
            for vals in itertools.product(*(s.controls[d].options for d in dims)):
                c = s.normalize(dict(zip(dims, vals, strict=True)))
                combos.setdefault(s.key(list(dims), c), c)
            if len(combos) > MAX_COMBOS:
                log.warning("%s %s: %d combinations, capped", s.id, dims, len(combos))
                combos = dict(list(combos.items())[:MAX_COMBOS])
            for key, c in combos.items():
                t = runner.run(c)
                if t is None:
                    s.skipped += 1
                    continue
                bs = blocks(t)
                for i in idxs:
                    s.variants.setdefault(i, {})[key] = _unit_html(r, bs, s.units[i])
    finally:
        r.section = None
        runner.reset()


def wrapper(s: Section) -> str:
    units = "".join(f'<div class="du" data-u="{i}">{h}</div>' for i, h in enumerate(s.initial))
    meta = html.escape(json.dumps(s.meta(), ensure_ascii=False), quote=True)
    st = html.escape(json.dumps(s.state(), ensure_ascii=False), quote=True)
    return f'<div class="dyn" data-meta="{meta}" data-state="{st}">{units}</div>'


def variants_json(s: Section) -> str:
    return json.dumps(
        {str(i): v for i, v in s.variants.items()}, ensure_ascii=False, separators=(",", ":")
    )


def digest(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


# ------------------------------------------------------------------ browser side

JS = r"""
// ---- working controls: pre-rendered blocks (server) + trace/row filters (client)
const variantFiles = {};
const metaOf = dyn => dyn._meta || (dyn._meta = JSON.parse(dyn.dataset.meta));
const stateOf = dyn => dyn._state || (dyn._state = JSON.parse(dyn.dataset.state));
const S = v => JSON.stringify(v);
function traceVisible(dyn, name) {
  const meta = metaOf(dyn), st = stateOf(dyn);
  for (const [k, rule] of Object.entries(meta.controls)) {
    if (rule.role !== 'client' || rule.kind !== 'traces') continue;
    const v = st.client[k];
    if (Array.isArray(rule.match)) {
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
    if (div.dataset.done && div.data) Plotly.restyle(div, {visible: div.data.map(t => traceVisible(dyn, t.name))});
  });
  const meta = metaOf(dyn), st = stateOf(dyn);
  for (const [k, rule] of Object.entries(meta.controls)) {
    if (rule.role !== 'client' || rule.kind !== 'rows') continue;
    const keep = st.client[k] || [];
    dyn.querySelectorAll('table').forEach(tbl => {
      const col = [...tbl.querySelectorAll('thead th')].findIndex(th => th.textContent.trim() === rule.column);
      if (col < 0) return;
      tbl.querySelectorAll('tbody tr').forEach(tr => {
        const cell = tr.children[col];
        tr.hidden = keep.length > 0 && !(cell && keep.includes(cell.textContent.trim()));
      });
    });
  }
}
function readCtl(c) {
  if (c.tagName === 'SELECT') return JSON.parse(c.value);
  if (c.type === 'checkbox') return c.checked;
  const on = [...c.querySelectorAll('button[aria-pressed=true]')].map(b => JSON.parse(b.dataset.v));
  return c.dataset.kind === 'multi' ? on : on[0];
}
function writeCtl(c, v) {
  if (c.tagName === 'SELECT') { c.value = S(v); return; }
  if (c.type === 'checkbox') { c.checked = !!v; return; }
  const want = c.dataset.kind === 'multi' ? (v || []).map(S) : [S(v)];
  c.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(want.includes(b.dataset.v))));
}
function syncCtls(dyn) {
  const st = stateOf(dyn);
  dyn.querySelectorAll('[data-k]').forEach(c => writeCtl(c, st[c.dataset.role][c.dataset.k]));
}
function blockKey(meta, server, dims) {
  const c = Object.assign({}, server);
  for (const [k, v, reset] of meta.fix_if) if (S(c[k]) === S(v)) reset.forEach(r => c[r] = meta.defaults[r]);
  return S(dims.map(d => c[d]));
}
async function serverChanged(dyn, key) {
  const meta = metaOf(dyn), st = stateOf(dyn);
  const units = Object.entries(meta.units).filter(([, dims]) => dims.includes(key));
  if (!units.length) return;
  dyn.classList.add('loading');
  try {
    variantFiles[meta.src] = variantFiles[meta.src] || fetch(meta.src).then(r => {
      if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
    const all = await variantFiles[meta.src];
    for (const [u, dims] of units) {
      const el = dyn.querySelector(':scope > .du[data-u="' + u + '"]');
      const views = all[u] || {};
      let html = views[blockKey(meta, st.server, dims)];
      if (html === undefined) {
        // e.g. a bond that isn't in the new type filter: take the closest view that keeps the
        // value just chosen, and adopt its other settings
        let best = null, score = -1;
        for (const k of Object.keys(views)) {
          const vals = JSON.parse(k);
          if (S(vals[dims.indexOf(key)]) !== S(st.server[key])) continue;
          const sc = dims.filter((d, i) => S(vals[i]) === S(st.server[d])).length;
          if (sc > score) { best = vals; score = sc; }
        }
        if (best) {
          dims.forEach((d, i) => st.server[d] = best[i]);
          html = views[S(best)];
        }
      }
      if (html === undefined) {
        el.querySelector('.dyn-miss') || el.insertAdjacentHTML('afterbegin',
          '<div class="alert warning dyn-miss">This combination isn\'t available on the static site; showing the previous view.</div>');
        continue;
      }
      el.innerHTML = html;
      drawVisible(el);
    }
    syncCtls(dyn);
    applyClient(dyn);
  } catch (e) {
    dyn.insertAdjacentHTML('afterbegin', '<div class="alert error">Could not load this view (' + e.message + ').</div>');
  } finally { dyn.classList.remove('loading'); }
}
function onCtl(dyn, c) {
  const st = stateOf(dyn), k = c.dataset.k, role = c.dataset.role;
  st[role][k] = readCtl(c);
  if (role === 'server') serverChanged(dyn, k); else applyClient(dyn);
}
document.querySelectorAll('.dyn').forEach(dyn => {
  syncCtls(dyn);
  dyn.addEventListener('change', e => {
    const c = e.target.closest('[data-k]');
    if (c && (c.tagName === 'SELECT' || c.type === 'checkbox')) onCtl(dyn, c);
  });
  dyn.addEventListener('click', e => {
    const b = e.target.closest('.seg button');
    if (!b || b.disabled) return;
    const c = b.closest('[data-k]');
    if (c.dataset.kind === 'multi') b.setAttribute('aria-pressed', String(b.getAttribute('aria-pressed') !== 'true'));
    else c.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    onCtl(dyn, c);
  });
});
"""
