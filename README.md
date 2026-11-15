# Chile Monitor

A personal Streamlit dashboard for Chilean monetary policy, macro, rates, FX and global macro.
It uses **public data only**. See `CLAUDE.md` for architecture, data sources and conventions,
and `docs/specs/` for feature specs.

## Setup

```bash
brew install uv                # once
uv sync                        # creates .venv with Python 3.12 + deps
cp .env.example .env           # then fill in your keys (BCCh token in single quotes)
```

Free keys:
- **BCCh BDE API token**: register at si3.bcentral.cl, then generate it under "Mi cuenta". It is valid for 1 year.
- **FRED**: https://fred.stlouisfed.org/docs/api/api_key.html
- **Banxico SIE**: https://www.banxico.org.mx/SieteAPI/

## Run

```bash
uv run python scripts/check_keys.py      # ✅/❌ per source
uv run streamlit run app/main.py         # dashboard
```

## Scheduled jobs (Bond Monitor)

Every scraper is plain Python and runs without the app (and without Claude). Each job writes to
`ingest_log`, which the Data Health tab shows.

| Job | When (New York time) | What |
|---|---|---|
| `monitor.jobs.auctions_daily` | Mon–Fri 07:30 | Hacienda calendar (with change log), latest result, SOMA authorization |
| `monitor.jobs.auctions_watch` | Mon–Fri 08:00 | On auction days only: polls the results page every 15 min, 11:05–18:00 Santiago, until today's result is stored |
| `monitor.jobs.auctions_evening` | Mon–Fri 18:00 | Results catch-up + current year's consolidated file |
| `monitor.jobs.bond_master_weekly` | Mon 07:00 | Bond master, outstanding, all consolidated files |

Run any of them by hand: `uv run python -m monitor.jobs.auctions_daily`. The scraper also has a
CLI: `uv run python -m monitor.sources.hacienda calendar|results|soma|master|backfill|watch|show|debug`.

**macOS (launchd, installed on this Mac):**

```bash
uv run python scripts/install_launchd.py --dry-run    # print the four plists
uv run python scripts/install_launchd.py              # install + load (~/Library/LaunchAgents)
uv run python scripts/install_launchd.py --uninstall  # remove
launchctl kickstart gui/$(id -u)/com.chilemonitor.auctions-daily   # run one now
tail -f data/logs/auctions-daily.log
```

launchd uses the Mac's local time and runs a job missed during sleep when the Mac wakes. The Mac
must be on (or wake) and you must be logged in.

**cron (Linux/macOS), equivalent:**

```cron
CRON_TZ=America/New_York
30 7 * * 1-5  cd /path/to/chile-monitor && ~/.local/bin/uv run python -m monitor.jobs.auctions_daily     >> data/logs/auctions-daily.log 2>&1
0  8 * * 1-5  cd /path/to/chile-monitor && ~/.local/bin/uv run python -m monitor.jobs.auctions_watch     >> data/logs/auctions-watch.log 2>&1
0 18 * * 1-5  cd /path/to/chile-monitor && ~/.local/bin/uv run python -m monitor.jobs.auctions_evening   >> data/logs/auctions-evening.log 2>&1
0  7 * * 1    cd /path/to/chile-monitor && ~/.local/bin/uv run python -m monitor.jobs.bond_master_weekly >> data/logs/bond-master-weekly.log 2>&1
```

The four launchd agents are **frozen** (disabled) since the morning job below replaced them.

## Deployment: morning job → static site on GitHub Pages

**Site:** https://arathreyes.github.io/chile-monitor/ (public; rebuilt every weekday morning).

```
GitHub Actions, Mon–Fri 05:05 New York (.github/workflows/morning.yml)
  1. download monitor.duckdb.gz + manual.tar.gz   (Release "data")
  2. refresh every source: BDE, INE, Kalshi/Polymarket, curves, Hacienda (Mondays: bond master)
  3. upload monitor.duckdb.gz
  4. run the real dashboard once and save every tab as HTML   (app/export_site.py)
  5. force-push the pages as one commit to the gh-pages branch  (monitor.pages)
  6. email the report (AgentMail)
```

- **Static pages from the real code.** `app/export_site.py` runs `app/main.py` with Streamlit's
  AppTest and converts what it draws (metrics, tables, captions, Plotly figures, sub-tabs,
  expanders, images) to HTML: one page per tab, sub-tabs switch in the browser, charts keep
  hover and zoom. Refresh buttons are dropped; filters/pickers show their default value as a chip.
  Build it locally: `uv run python app/export_site.py --out site` and open `site/index.html`.
- **Schedule:** GitHub cron is UTC without DST, so it fires at 09:05 and 10:05 UTC and
  `--at-ny-hour 5` keeps only the one that is 05:xx in New York; a second run the same NY day is
  skipped. GitHub may start scheduled runs late. Run now: Actions → morning → Run workflow.
- **Every step runs even if an earlier one fails.** The email lists OK/WARN/FAIL per step with
  errors, the publish result and site link, "data as of" for key series, and the log (tail
  inline, full log attached). If the published database can't be downloaded nothing is
  published (never overwrites good data with an empty file).
- **Storage:** the database and `data/manual` (saved BCCh pages, PDFs, board photos; without
  the `*_files` page-asset folders) are Release assets, never in git history. gh-pages is a
  single commit replaced daily. By hand: `uv run python -m monitor.release
  status|pull|push|pull-manual|push-manual`.
- **New BCCh documents** (bcentral.cl is bot-protected, saved by hand on the Mac):
  `release pull`, `uv run python -m monitor.jobs.mp_daily`, `release push`, `release push-manual`.
- **Secrets / variables:** secrets `BCCH_API_TOKEN`, `AGENTMAIL_API_KEY`; variables
  `AGENTMAIL_INBOX`, `REPORT_TO`. The workflow's own `GITHUB_TOKEN` uploads assets and pushes
  gh-pages. GitHub Pages: Settings → Pages → Deploy from branch `gh-pages` / root.
- **Optional live app:** `app/bootstrap.py` lets a hosted Streamlit app (e.g. Community Cloud)
  pull the published database when `AUTO_PULL_DB=1`; dependencies in `requirements.txt`.

## Checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest                  # offline tests (fixtures)
uv run pytest -m live          # live-API tests (needs keys)
```

## Layout

```
config/        series registry, meetings, sources, themes (YAML)
src/monitor/   config, storage (DuckDB), parsing, http, sources/, models/, jobs/
app/           Streamlit entry point, shared components, one module per tab
scripts/       check_keys.py, install_launchd.py, one-off maintenance scripts
tests/         pytest + saved fixtures
data/          DuckDB + raw downloads (gitignored)
```
