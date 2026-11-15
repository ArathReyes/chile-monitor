"""Static site exporter (app/export_site.py), gh-pages commit and manual-folder archive."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import export_site as ex  # noqa: E402

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
