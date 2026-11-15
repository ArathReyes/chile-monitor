"""Static site exporter (app/export_site.py), gh-pages commit and manual-folder archive."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import export_site as ex  # noqa: E402
import site_dynamic as sd  # noqa: E402

from monitor import pages, release  # noqa: E402


def test_markdown_subset():
    h = ex.markdown("#### Title\n\n**bold** and *it* `x<y` [a](https://e.com) :red[hot]")
    assert "<h5>Title</h5>" in h
    assert "<strong>bold</strong>" in h and "<em>it</em>" in h
    assert "<code>x&lt;y</code>" in h
    assert '<a href="https://e.com"' in h and 'style="color:#cf222e">hot</span>' in h


def test_markdown_escapes_html_unless_allowed():
    assert "&lt;b&gt;" in ex.markdown("<b>x</b>")
    assert "<b>x</b>" in ex.markdown("<b>x</b>", allow_html=True)


def test_markdown_lists_and_tables():
    h = ex.markdown("- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<ul><li>one</li><li>two</li></ul>" in h
    assert "<th>a</th>" in h and "<td>2</td>" in h


def test_markdown_does_not_italicise_identifiers():
    assert "<em>" not in ex.markdown("cl_spc_nom_2y and 2*3*4")


def test_dataframe_formats_cells():
    df = pd.DataFrame(
        {"x": [1234.5678, None], "d": pd.to_datetime(["2026-11-13", None]), "s": ["<a>", "b"]}
    )
    h = ex.dataframe(df)
    assert "1,234.57" in h and "2026-11-13" in h and "&lt;a&gt;" in h
    assert "None" not in h and "NaT" not in h and "nan" not in h


def test_slug():
    assert ex.slug("Curves & Rates") == "curves-rates"


def _git(repo: Path, *a: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *a], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_commit_site_is_orphan_and_leaves_worktree(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "code.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>hi</h1>")
    (site / ".nojekyll").write_text("")
    sha = pages.commit_site(repo, site, "deploy")
    assert _git(repo, "rev-list", "--parents", "-n1", sha) == sha  # no parent
    files = set(_git(repo, "ls-tree", "--name-only", sha).split())
    assert files == {"index.html", ".nojekyll"}
    assert _git(repo, "status", "--porcelain") == ""  # main worktree untouched
    assert _git(repo, "branch", "--show-current") == "main"


def test_site_url():
    assert (
        pages.site_url("ArathReyes/chile-monitor") == "https://arathreyes.github.io/chile-monitor/"
    )


def test_manual_archive_skips_saved_page_assets(tmp_path: Path):
    src = tmp_path / "data" / "manual"
    (src / "board" / "Board-Pictures").mkdir(parents=True)
    (src / "board" / "Jane - BCCh_files").mkdir()
    (src / "board" / "Jane - BCCh.html").write_text("profile")
    (src / "board" / "Board-Pictures" / "jane.jpg").write_bytes(b"\xff\xd8\xff")
    (src / "board" / "Jane - BCCh_files" / "app.js").write_text("js")
    arc = tmp_path / "m.tar.gz"
    release.tar_manual(src, arc)
    out = tmp_path / "out"
    release.untar_manual(arc, out)
    assert (out / "manual" / "board" / "Jane - BCCh.html").read_text() == "profile"
    assert (out / "manual" / "board" / "Board-Pictures" / "jane.jpg").exists()
    assert not (out / "manual" / "board" / "Jane - BCCh_files").exists()


class _W:  # minimal stand-in for an AppTest widget node
    def __init__(self, type, key, value, options=(), label="L", pmin=""):
        self.type, self.key, self.value, self.options, self.label = type, key, value, options, label
        self.proto = SimpleNamespace(min=pmin, disabled=False)


def test_jv_matches_javascript_json():
    from datetime import date

    assert sd.jkey([1000.0, 0.005, True, "√", (date(2026, 1, 2), date(2026, 3, 4))]) == (
        '[1000,0.005,true,"√",["2026-01-02","2026-03-04"]]'
    )


def test_control_kinds():
    assert sd.control_for(_W("selectbox", "x", "a", ["a", "b"])).options == ["a", "b"]
    assert sd.control_for(_W("toggle", "t", False)).options == [False, True]
    assert sd.control_for(_W("button", "b", False)) is None
    assert sd.control_for(_W("text_input", "s", "1, 2")) is None  # free text stays fixed
    big = sd.control_for(_W("selectbox", "x", "a", [str(i) for i in range(sd.MAX_OPTIONS + 1)]))
    assert big is None
    m = sd.control_for(_W("multiselect", "m", ["a", "b"], ["a", "b"]))
    assert m.options == [["a", "b"], ["a"], ["b"]]
    assert sd.control_for(_W("multiselect", "m", [], [str(i) for i in range(9)])) is None


def test_slider_grid_and_client_controls():
    c = sd.control_for(_W("slider", "mp_lam", 0.005))
    assert c.role == "server" and 0.005 in c.options
    assert sd.control_for(_W("slider", "unlisted", 0.3)) is None
    h = sd.control_for(_W("multiselect", "cr_h", list(sd.HORIZONS), list(sd.HORIZONS)))
    assert h.role == "client" and h.rule["kind"] == "traces"


def test_date_range_presets_include_default():
    from datetime import date

    today = date(2026, 11, 15)
    default = (date(2024, 11, 15), date(2026, 11, 12))
    c = sd.control_for(_W("date_input", "bm_r_d", default, pmin="2021/01/05"), today)
    assert c.labels == ["Last 1Y", "Last 2Y", "Last 5Y", "All"]
    assert sd.jkey([c.options[1]]) == sd.jkey([default]) or "Default" in c.labels
    assert c.options[-1][0] == date(2021, 1, 5)


def _section() -> sd.Section:
    ctl = {
        k: sd.Control(k, "selectbox", k, opts, [str(o) for o in opts], opts[0], "server")
        for k, opts in {
            "cr_series": ["Nominal swaps", "Real swaps"],
            "cr_mode": ["Values", "Deltas"],
            "cr_window": ["6M", "1Y", "2W"],
            "cr_grid": [False, True],
        }.items()
    }
    return sd.Section(("Curves & Rates", "Curve Snapshot"), ctl)


def test_section_collapses_equivalent_combinations():
    s = _section()
    dims = ["cr_series", "cr_mode", "cr_window", "cr_grid"]
    a = {"cr_series": "Real swaps", "cr_mode": "Deltas", "cr_window": "1Y", "cr_grid": False}
    b = dict(a, cr_window="2W")
    assert s.key(dims, a) == s.key(dims, b) == '["Real swaps","Deltas","6M",false]'
    g = dict(a, cr_mode="Values", cr_grid=True)
    assert s.key(dims, g) == '["Nominal swaps","Values","1Y",true]'
    assert s.id == "curves-rates-curve-snapshot"


def test_meta_lists_only_dependent_blocks():
    s = _section()
    s.deps = [[], ["cr_series"], []]
    m = s.meta()
    assert m["units"] == {"1": ["cr_series"]}
    assert m["src"] == "dyn-curves-rates-curve-snapshot.json"


def test_client_keys_exist_in_app_code():
    app = Path(__file__).resolve().parents[1] / "app" / "tabs"
    code = "".join(p.read_text() for p in app.glob("*.py"))
    for k in [*sd.CLIENT, *sd.VALUES, *sd.DATE_RANGES, *(f[0] for f in sd.FIX_IF)]:
        assert f'key="{k}"' in code or f"key='{k}'" in code, k
