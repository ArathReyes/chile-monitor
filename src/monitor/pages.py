"""Publish the static site (app/export_site.py output) to the `gh-pages` branch.

Each deploy is a single parentless commit force-pushed to gh-pages, so the branch never grows:
yesterday's pages are simply replaced. Uses git plumbing with a temporary index, so the working
tree and the current branch are never touched; the push uses whatever credentials `origin`
already has (actions/checkout's token on GitHub Actions, your login locally).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

BRANCH = "gh-pages"
BOT = ("chile-monitor bot", "41898282+github-actions[bot]@users.noreply.github.com")


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=False
    )
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()[-400:]}")
    return r.stdout.strip()


def site_url(repo_slug: str) -> str:
    owner, name = repo_slug.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


def commit_site(repo: Path, site: Path, message: str) -> str:
    """Commit the contents of `site` as an orphan commit; returns its sha (nothing pushed)."""
    if not (site / "index.html").exists():
        raise RuntimeError(f"{site}/index.html missing: build the site first")
    with tempfile.TemporaryDirectory() as d:
        env = {
            **os.environ,
            "GIT_INDEX_FILE": str(Path(d) / "index"),
            "GIT_AUTHOR_NAME": BOT[0],
            "GIT_AUTHOR_EMAIL": BOT[1],
            "GIT_COMMITTER_NAME": BOT[0],
            "GIT_COMMITTER_EMAIL": BOT[1],
        }
        _git(repo, f"--work-tree={site.resolve()}", "add", "-A", "--force", ".", env=env)
        tree = _git(repo, "write-tree", env=env)
        return _git(repo, "commit-tree", tree, "-m", message, env=env)


def deploy(repo: Path, site: Path, message: str, remote: str = "origin") -> str:
    sha = commit_site(repo, site, message)
    _git(repo, "push", "--force", remote, f"{sha}:refs/heads/{BRANCH}")
    return sha
