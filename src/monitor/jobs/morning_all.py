"""Morning job: bring EVERYTHING up to date, publish the database, email a report.

Steps run in order on one connection; a failing step is recorded and the next one still runs.
Then the database is published as a GitHub Release asset (monitor.release) and a report goes
out by email (monitor.notify): OK/WARN/FAIL per step, rows, errors, data freshness, log tail;
the full log is attached.

Not run here: BCCh web documents (statements, minutes, EEE PDFs) are saved by hand on the Mac
(bcentral.cl is bot-protected): pull, run `monitor.jobs.mp_daily`, push (see README).

Schedule (GitHub Actions): Mon-Fri 05:05 New York. GitHub cron is UTC and ignores DST, so the
workflow fires at 09:05 and 10:05 UTC and `--at-ny-hour 5` lets only the one that is 05:xx in
New York through; a run is also skipped if one already succeeded today (NY date).

    uv run python -m monitor.jobs.morning_all                    # everything, no upload/email
    uv run python -m monitor.jobs.morning_all --publish --email  # what the workflow runs
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from monitor import notify, pages, release, storage
from monitor.config import get_secret
from monitor.http import redact
from monitor.jobs import bcch_series, curves_daily, macro_daily, snapshot_markets
from monitor.sources import hacienda as h
from monitor.sources.ine import IneSource

log = logging.getLogger("monitor.morning")
NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
JOB = "morning_all"
REPO = Path(__file__).resolve().parents[3]
SITE_DIR = REPO / "site"

# series shown in the email's freshness table: (label, series_id)
FRESHNESS = [
    ("TPM", "cl_tpm"),
    ("UF", "cl_uf"),
    ("USD/CLP observado", "cl_usdclp"),
    ("Cámara swap 2Y", "cl_spc_nom_2y"),
    ("BCP 10Y", "cl_bcp_10y"),
    ("Cámara UF 5Y", "cl_spc_uf_5y"),
    ("CPI m/m", "cl_cpi_mm_hist"),
    ("IMACEC", "cl_imacec"),
]


@dataclass
class StepResult:
    name: str
    status: str  # ok | warn | fail
    detail: str
    seconds: float
    errors: list[str] = field(default_factory=list)


def classify(result: object) -> tuple[str, str, list[str]]:
    """(status, one-line detail, error lines) from a job's return value."""
    if isinstance(result, str) and result.startswith("error"):
        return "fail", result, [result]
    if isinstance(result, dict):
        errs = [f"{k}: {v}" for k, v in result.items() if isinstance(v, str) and "error" in v]
        nums = [
            v for v in result.values() if isinstance(v, int | float) and not isinstance(v, bool)
        ]
        detail = f"{len(result)} items"
        if nums:
            detail += f", {int(sum(nums))} rows changed"
        if errs:
            status = "fail" if len(errs) == len(result) else "warn"
            return status, f"{detail}, {len(errs)} errors", errs
        return "ok", detail, []
    return "ok", str(result), []


def _ine(conn) -> int:
    with storage.ingest_run(conn, "macro_daily.ine_calendar", "ine") as r:
        r.rows = storage.upsert_releases(conn, IneSource().fetch())
    return r.rows


def _surprises(conn) -> int:
    with storage.ingest_run(conn, "macro_daily.surprises", "derived") as r:
        r.rows = storage.replace_release_surprises(conn, macro_daily.compute_surprises(conn))
    return r.rows


def _derived(conn) -> int:
    with storage.ingest_run(conn, "curves_daily.derived_spreads", "derived") as r:
        r.rows = curves_daily.compute_derived(conn)
    return r.rows


def _fits(conn) -> dict:
    with storage.ingest_run(conn, "curves_daily.nss_fits", "derived") as r:
        fits = curves_daily.compute_fits(conn)
        r.rows = sum(fits.values())
    return fits


def _consolidated(conn) -> object:
    return h.backfill(conn, [h.santiago_today().year])


def steps(today: date) -> list[tuple[str, Callable[[duckdb.DuckDBPyConnection], object]]]:
    out: list[tuple[str, Callable]] = [
        ("BCCh BDE series (all tabs)", lambda c: bcch_series.run(conn=c)),
        ("INE release calendar", _ine),
        ("Macro surprises", _surprises),
        ("Curve derived spreads", _derived),
        ("Curve NSS fits", _fits),
        ("Kalshi / Polymarket", lambda c: snapshot_markets.run(candles=True, conn=c)),
        ("Hacienda auction calendar", h.run_calendar),
        ("Hacienda latest result", h.run_results),
        ("Hacienda SOMA authorization", h.run_soma),
        ("Hacienda consolidated (this year)", _consolidated),
    ]
    if today.weekday() == 0:  # Mondays: every consolidated file + bond master
        out += [
            ("Hacienda consolidated (all years)", lambda c: h.backfill(c)),
            ("Bond master + outstanding", h.run_master),
        ]
    return out


def run_steps(conn, plan) -> list[StepResult]:
    results = []
    for name, fn in plan:
        t0 = time.monotonic()
        log.info("== %s", name)
        try:
            status, detail, errs = classify(fn(conn))
        except Exception as e:  # noqa: BLE001  (one failed step must not stop the rest)
            log.error("%s failed:\n%s", name, traceback.format_exc())
            status, detail, errs = "fail", f"{type(e).__name__}: {e}", [f"{type(e).__name__}: {e}"]
        dt = time.monotonic() - t0
        log.info("   %s (%s) in %.1fs", status.upper(), detail, dt)
        results.append(StepResult(name, status, detail, dt, errs))
    return results


def freshness(conn) -> list[tuple[str, str]]:
    rows = [(label, str(storage.last_obs_date(conn, sid) or "none")) for label, sid in FRESHNESS]
    k = storage.latest_snapshot_ts(conn, "kalshi")
    rows.append(("Kalshi snapshot", _ny(k) if k else "none"))
    rows.append(("Latest auction result", str(storage.latest_auction_result_date(conn) or "none")))
    missing = h.missing_result_dates(conn)
    rows.append(("Auction results missing", ", ".join(map(str, missing)) or "none"))
    return rows


def _ny(ts: datetime) -> str:
    return ts.replace(tzinfo=UTC).astimezone(NY).strftime("%Y-%m-%d %H:%M NY")


def overall(results: list[StepResult]) -> str:
    st = {r.status for r in results}
    return "FAILED" if "fail" in st else "WARNINGS" if "warn" in st else "OK"


def report(
    results: list[StepResult],
    fresh: list[tuple[str, str]],
    publish: str,
    started: datetime,
    log_tail: str,
) -> tuple[str, str, str]:
    """(subject, text, html)."""
    verdict = overall(results)
    n_bad = sum(r.status != "ok" for r in results)
    subject = f"Chile Monitor morning run {started:%a %d-%b}: {verdict}"
    if n_bad:
        subject += f" ({n_bad} of {len(results)} steps)"
    lines = [f"Chile Monitor morning run, started {_ny(started)}: {verdict}", "", "Steps:"]
    for r in results:
        lines.append(f"  [{r.status.upper():4s}] {r.name}: {r.detail} ({r.seconds:.0f}s)")
        lines += [f"         - {e}" for e in r.errors[:15]]
    lines += ["", f"Publish: {publish}", "", "Data as of:"]
    lines += [f"  {k}: {v}" for k, v in fresh]
    lines += ["", "Log tail:", log_tail]
    text = "\n".join(lines)

    color = {"ok": "#1a7f37", "warn": "#9a6700", "fail": "#cf222e"}
    rows = "".join(
        f"<tr><td style='color:{color[r.status]};font-weight:600'>{r.status.upper()}</td>"
        f"<td>{escape(r.name)}</td><td>{escape(r.detail)}</td><td>{r.seconds:.0f}s</td></tr>"
        + (
            "<tr><td></td><td colspan=3 style='color:#57606a;font-size:12px'>"
            + "<br>".join(escape(e) for e in r.errors[:15])
            + "</td></tr>"
            if r.errors
            else ""
        )
        for r in results
    )
    frows = "".join(f"<tr><td>{escape(k)}</td><td>{escape(v)}</td></tr>" for k, v in fresh)
    html = (
        "<div style='font-family:-apple-system,Segoe UI,sans-serif;font-size:14px'>"
        f"<h2 style='margin:0 0 8px'>Chile Monitor · {verdict}</h2>"
        f"<p style='color:#57606a'>Started {_ny(started)}</p>"
        f"<table cellpadding=4 style='border-collapse:collapse'>{rows}</table>"
        f"<p><b>Publish:</b> {escape(publish)}</p>"
        f"<h3>Data as of</h3><table cellpadding=3>{frows}</table>"
        f"<h3>Log tail</h3><pre style='font-size:12px;background:#f6f8fa;padding:8px'>"
        f"{escape(log_tail)}</pre></div>"
    )
    return subject, text, html


def _timed(name: str, fn: Callable[[], object]) -> StepResult:
    return run_steps(None, [(name, lambda _c: fn())])[0]


def build_site(out: Path) -> dict:
    """Run app/export_site.py in a subprocess (src/ never imports Streamlit)."""
    r = subprocess.run(
        [sys.executable, str(REPO / "app" / "export_site.py"), "--out", str(out)],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=1800,
        check=False,
    )
    last = (r.stdout.strip().splitlines() or ["{}"])[-1]
    try:
        res = json.loads(last)
    except json.JSONDecodeError:
        return {"build": f"error: exit {r.returncode}: {r.stderr.strip()[-400:]}"}
    if res.get("unknown_elements"):
        log.warning("site: elements not rendered: %s", res["unknown_elements"])
    summary = f"{res['pages']} pages, {res['plots']} charts, {res['bytes'] / 1e6:.1f} MB"
    if not res.get("app_exceptions"):
        return summary
    # a tab raised: still publish (warn), the other tabs are fine
    return {"site": summary} | {
        f"app exception {i}": f"error: {e}" for i, e in enumerate(res["app_exceptions"], 1)
    }


def should_run(now_utc: datetime, at_ny_hour: int | None, last_ok: datetime | None) -> str | None:
    """Reason to skip, or None to run."""
    now_ny = now_utc.astimezone(NY)
    if at_ny_hour is not None and now_ny.hour != at_ny_hour:
        return f"it is {now_ny:%H:%M} in New York, not {at_ny_hour:02d}:xx"
    if last_ok is not None and last_ok.replace(tzinfo=UTC).astimezone(NY).date() == now_ny.date():
        return f"already ran successfully today ({_ny(last_ok)})"
    return None


def _setup_logging() -> io.StringIO:
    buf = io.StringIO()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(buf), logging.StreamHandler(sys.stdout)):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs may carry the token
    return buf


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pull", action="store_true", help="download the published DB first")
    p.add_argument("--publish", action="store_true", help="upload the DB as a release asset")
    p.add_argument("--email", action="store_true", help="email the report")
    p.add_argument("--site", action="store_true", help="build the static site into site/")
    p.add_argument("--deploy-site", action="store_true", help="push site/ to gh-pages")
    p.add_argument("--at-ny-hour", type=int, default=None, help="skip unless NY hour matches")
    p.add_argument("--force", action="store_true", help="ignore the once-a-day guard")
    a = p.parse_args(argv)
    buf = _setup_logging()
    started = datetime.now(UTC)

    skip = should_run(started, a.at_ny_hour, None)
    if skip:
        log.info("skipping: %s", skip)
        return 0
    pre: list[StepResult] = []
    if a.pull:
        try:
            info = release.pull()
        except Exception as e:  # noqa: BLE001
            # never go on (and publish) from an empty/old database: report and stop
            log.error("pull failed:\n%s", traceback.format_exc())
            msg = f"Could not download the published database: {type(e).__name__}: {e}"
            if a.email:
                notify.send_email("Chile Monitor morning run: FAILED (download)", msg)
            return 1
        log.info("pulled published DB: %s", info or "none published yet, starting empty")
        pre.append(_timed("Download manual files (board, statements)", release.pull_manual))
    conn = storage.connect()
    try:
        last_ok = None if a.force else storage.last_job_success(conn, JOB)
        skip = should_run(started, None, last_ok)
        if skip:
            log.info("skipping: %s", skip)
            return 0
        today = started.astimezone(NY).date()
        results = pre + run_steps(conn, steps(today))
        storage.log_ingest(
            conn,
            JOB,
            "all",
            started.replace(tzinfo=None),
            storage.utcnow(),
            "ok" if overall(results) != "FAILED" else "error",
            sum(r.status == "ok" for r in results),
            None
            if overall(results) != "FAILED"
            else "; ".join(r.name for r in results if r.status == "fail"),
        )
        fresh = freshness(conn)
    finally:
        conn.close()

    if a.site:  # after conn.close(): the app opens the database itself
        results.append(_timed("Build static site", lambda: build_site(SITE_DIR)))
    publish = "skipped (no --publish)"
    if a.publish:
        try:
            info = release.push()
            publish = f"OK, {info.size / 1e6:.1f} MB at {_ny(info.updated_at.replace(tzinfo=None))}"
        except Exception as e:  # noqa: BLE001
            log.error("publish failed:\n%s", traceback.format_exc())
            publish = f"FAILED: {type(e).__name__}: {e}"
            results.append(StepResult("Publish database", "fail", publish, 0, [publish]))
    if a.deploy_site:
        if results[-1].name == "Build static site" and results[-1].status != "fail":
            msg = f"Chile Monitor data as of {datetime.now(NY):%Y-%m-%d %H:%M} NY"
            r = _timed("Deploy site (gh-pages)", lambda: pages.deploy(REPO, SITE_DIR, msg)[:7])
            results.append(r)
            if r.status == "ok" and (slug := get_secret("DATA_REPO")):
                publish += f"; site {pages.site_url(slug)}"
        else:
            results.append(StepResult("Deploy site (gh-pages)", "fail", "site not built", 0))
    log.info("publish: %s", publish)

    full_log = redact(buf.getvalue())  # belt and braces: no secret ever leaves in an email
    tail = "\n".join(full_log.splitlines()[-40:])
    subject, text, html = report(results, fresh, publish, started, tail)
    print(text)
    if a.email:
        att = notify.Attachment(f"morning-{today}.log", full_log.encode())
        try:
            log.info("email sent: %s", notify.send_email(subject, text, html, [att]))
        except Exception:  # noqa: BLE001
            log.error("email failed:\n%s", traceback.format_exc())
            return 2
    return 1 if overall(results) == "FAILED" else 0


if __name__ == "__main__":
    sys.exit(main())
