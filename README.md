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
scripts/       check_keys.py
tests/         pytest + saved fixtures
data/          DuckDB + raw downloads (gitignored)
```
