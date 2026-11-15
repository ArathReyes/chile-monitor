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

**GitHub Actions (example, not active):** the jobs need the DuckDB file between runs, so a hosted
runner must restore and save `data/monitor.duckdb` (e.g. as an artifact or in object storage)
around each run. GitHub cron is UTC (07:30 New York = 12:30 UTC in winter, 11:30 in summer).

```yaml
name: auctions-daily
on:
  schedule: [{cron: "30 12 * * 1-5"}]
  workflow_dispatch: {}
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      # restore data/monitor.duckdb here
      - run: uv run python -m monitor.jobs.auctions_daily
      # save data/monitor.duckdb here
```

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
