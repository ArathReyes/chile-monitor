"""Ship the DuckDB file through a GitHub Release asset (the deployed app's data).

The morning job uploads `monitor.duckdb.gz` to the release tagged `data` of DATA_REPO; the
Streamlit app downloads it on start (and when a newer one appears). The database never goes
into git history.

Config: DATA_REPO (owner/name), GITHUB_TOKEN or GH_TOKEN (contents read; write to upload).

    uv run python -m monitor.release status
    uv run python -m monitor.release pull    # replace data/monitor.duckdb with the published one
    uv run python -m monitor.release push    # publish data/monitor.duckdb
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from monitor import storage
from monitor.config import get_secret

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
TAG = "data"
ASSET = "monitor.duckdb.gz"


@dataclass
class Remote:
    asset_id: int
    updated_at: datetime
    size: int


def _repo() -> str:
    repo = get_secret("DATA_REPO")
    if not repo:
        raise RuntimeError("DATA_REPO (owner/name) is not set")
    return repo


def _client(token: str | None = None) -> httpx.Client:
    token = token or get_secret("GITHUB_TOKEN") or get_secret("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(headers=headers, timeout=120, follow_redirects=True)


def _release(c: httpx.Client, repo: str, create: bool = False) -> dict:
    r = c.get(f"{API}/repos/{repo}/releases/tags/{TAG}")
    if r.status_code == 404 and create:
        r = c.post(
            f"{API}/repos/{repo}/releases",
            json={"tag_name": TAG, "name": "Data", "body": "Daily DuckDB snapshot (morning job)."},
        )
    r.raise_for_status()
    return r.json()


def _asset(rel: dict, name: str) -> dict | None:
    return next((a for a in rel.get("assets", []) if a["name"] == name), None)


def remote_info(c: httpx.Client | None = None) -> Remote | None:
    own = c is None
    c = c or _client()
    try:
        r = c.get(f"{API}/repos/{_repo()}/releases/tags/{TAG}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        a = _asset(r.json(), ASSET)
        if a is None:
            return None
        ts = datetime.fromisoformat(a["updated_at"].replace("Z", "+00:00"))
        return Remote(a["id"], ts, a["size"])
    finally:
        if own:
            c.close()


def gzip_file(src: Path, dst: Path) -> None:
    with open(src, "rb") as f, gzip.open(dst, "wb", compresslevel=6) as g:
        shutil.copyfileobj(f, g, 1 << 20)


def gunzip_to(data_gz: Path, dst: Path) -> None:
    """Decompress next to dst, then atomically replace dst (readers never see half a file)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dst.parent, suffix=".part")
    os.close(fd)
    try:
        with gzip.open(data_gz, "rb") as g, open(tmp, "wb") as f:
            shutil.copyfileobj(g, f, 1 << 20)
        wal = dst.with_name(dst.name + ".wal")
        wal.unlink(missing_ok=True)  # a stale WAL would be replayed onto the new file
        os.replace(tmp, dst)
    finally:
        Path(tmp).unlink(missing_ok=True)


def pull(dst: Path | None = None, c: httpx.Client | None = None) -> Remote | None:
    """Download the published database to dst (default data/monitor.duckdb). None if absent."""
    dst = Path(dst or storage.DEFAULT_DB_PATH)
    own = c is None
    c = c or _client()
    try:
        info = remote_info(c)
        if info is None:
            return None
        url = f"{API}/repos/{_repo()}/releases/assets/{info.asset_id}"
        with tempfile.TemporaryDirectory() as d:
            gz = Path(d) / ASSET
            with c.stream("GET", url, headers={"Accept": "application/octet-stream"}) as r:
                r.raise_for_status()
                with open(gz, "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
            gunzip_to(gz, dst)
        return info
    finally:
        if own:
            c.close()


def push(src: Path | None = None, c: httpx.Client | None = None) -> Remote:
    """Checkpoint, gzip and publish src. Uploads under a temporary name, then swaps it in, so
    the published asset is missing only for the instant between delete and rename."""
    src = Path(src or storage.DEFAULT_DB_PATH)
    conn = storage.connect(src)
    try:
        storage.checkpoint(conn)
    finally:
        conn.close()
    repo = _repo()
    own = c is None
    c = c or _client()
    try:
        rel = _release(c, repo, create=True)
        tmp_name = ASSET + ".new"
        if (stale := _asset(rel, tmp_name)) is not None:  # left by an interrupted run
            c.delete(f"{API}/repos/{repo}/releases/assets/{stale['id']}").raise_for_status()
        with tempfile.TemporaryDirectory() as d:
            gz = Path(d) / ASSET
            gzip_file(src, gz)
            r = c.post(
                f"{UPLOADS}/repos/{repo}/releases/{rel['id']}/assets",
                params={"name": tmp_name},
                content=gz.read_bytes(),
                headers={"Content-Type": "application/gzip"},
            )
            r.raise_for_status()
            new = r.json()
        if (old := _asset(rel, ASSET)) is not None:
            c.delete(f"{API}/repos/{repo}/releases/assets/{old['id']}").raise_for_status()
        r = c.patch(f"{API}/repos/{repo}/releases/assets/{new['id']}", json={"name": ASSET})
        r.raise_for_status()
        a = r.json()
        return Remote(
            a["id"], datetime.fromisoformat(a["updated_at"].replace("Z", "+00:00")), a["size"]
        )
    finally:
        if own:
            c.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["status", "pull", "push"])
    a = p.parse_args()
    if a.action == "status":
        print(remote_info() or "no published database")
    elif a.action == "pull":
        print(pull() or "no published database")
    else:
        print(push())


if __name__ == "__main__":
    main()
