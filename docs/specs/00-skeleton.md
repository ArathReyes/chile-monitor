# Spec 00 — Project skeleton

Status: **done** (2026-11-14). This is Part B of the original setup prompt, kept for reference.

## Deviations from the original prompt (decided during the build)
- **BCCh auth:** the BDE REST API uses an API Key Token passed as `?token=` (confirmed in the official docs). It is called directly with httpx; the `bcchapi` wrapper is not used. `siete.buscar()` is replaced by `monitor.sources.bcch.search_series(frequency, query)`.
- **BDE encoding:** responses are `charset=ISO-8859-1`; parse with `json.loads(resp.text)`, not `resp.json()`.
- **Extra deps** (approved): `pyyaml`, `pydantic`, `python-dotenv`.
- **`app/tabs/calendar.py` → `calendar_tab.py`** (avoid shadowing stdlib `calendar`).
- **Series codes verified** via SearchSeries on 2026-11-14 (TPM, SPC ×4, IMACEC, CPI); see `config/series.yaml`.
- `observations_history` mirrors `observations`; `auctions` has provisional columns.
- Added `src/monitor/health.py` (pure staleness rules) for the Data Health tab.

---

## Repo layout
```
chile-monitor/
├── CLAUDE.md
├── README.md                 # setup: uv sync, copy .env.example → .env, run app, run checks
├── pyproject.toml            # uv-managed
├── .env.example              # BCCH_API_TOKEN=, FRED_API_KEY=, BANXICO_TOKEN=, EIA_API_KEY=, ANTHROPIC_API_KEY=
├── .gitignore                # .env, data/, .venv, __pycache__
├── config/
│   ├── series.yaml           # registry with a handful of example entries
│   ├── meetings.yaml         # structure only + TODO to populate from bcentral.cl / federalreserve.gov
│   ├── sources.yaml          # base URLs and rate limits for every source in CLAUDE.md
│   └── themes/
│       ├── oil_middle_east.yaml
│       └── ai_capex.yaml
├── data/                     # gitignored; created at runtime
├── docs/specs/
│   └── 00-skeleton.md
├── src/monitor/
│   ├── __init__.py
│   ├── config.py             # pydantic models + loaders for the YAML files and .env
│   ├── storage.py            # DuckDB connection, schema creation (all tables in CLAUDE.md), upsert helpers
│   ├── parsing.py            # Chilean number/date parsers (with tests)
│   ├── http.py               # shared httpx client: timeouts, retries (tenacity), User-Agent, per-host rate limit
│   ├── sources/
│   │   ├── base.py           # Source protocol/ABC: name, fetch(...)->DataFrame, health_check()
│   │   ├── bcch.py           # health_check (token works, one small series fetch)
│   │   ├── fred.py           # health_check only
│   │   ├── kalshi.py         # health_check only (GET public markets for series KXCBDCHILE)
│   │   ├── banxico.py        # health_check only
│   │   └── ...               # stubs: polymarket, yfinance_src, bcb, hacienda, bcch_web, ine
│   ├── models/__init__.py    # empty for now
│   └── jobs/__init__.py      # empty for now
├── app/
│   ├── main.py               # st.set_page_config(layout="wide"), title, top-level st.tabs for all 10 tabs
│   ├── components.py         # placeholder_card(), source_badge(), stale_badge(), refresh_button()
│   └── tabs/                 # one module per tab, each exposing render()
├── scripts/
│   └── check_keys.py         # runs every source's health_check() and prints a ✅/❌ table
└── tests/
    ├── test_parsing.py
    ├── test_storage.py
    └── fixtures/
```

## Placeholder tabs
Each tab module exposes `render()`. Until a tab is implemented, `render()` shows a `placeholder_card` with:
- **Purpose** (1–2 lines)
- **Planned features** (bullets, from the Tabs section of CLAUDE.md)
- **Data sources** (table: feature → source → access method → key needed → frequency/lag)
- **Status:** "Not implemented — see docs/specs/"
- A disabled **Refresh** button

Monetary Policy renders five sub-tabs: Overview, Board & Communication, Decision History, Implied Path & Probabilities, Surveys.

## Example `series.yaml` entries
- TPM daily: BDE `F022.TPM.TIN.D001.NO.Z.D`
- SPC nominal 10y: BDE `F022.SPC.TIN.AN10.NO.Z.D`
- SPC UF 2y: BDE `F022.SPC.TIN.AN02.UF.Z.D`
- SPC nominal 90d traded (360 basis, T-1): BDE `F022.SPC.TPR.D090.NO.Z.D`
- SPC nominal 360d traded: BDE `F022.SPC.TPR.D360.NO.Z.D`
- IMACEC: BDE `F032.IMC.IND.Z.Z.EP18.Z.Z.0.M` (found via SearchSeries)
- CPI base 2023: BDE `F074.IPC.IND.Z.2023.C.M` (found via SearchSeries)
- UST 10y: FRED `DGS10`; UST 2y: FRED `DGS2`

## Data Health tab
Real, not a placeholder. Reads `ingest_log` and `series_meta`; shows an empty state explaining how to populate them while empty, plus the configured series registry.

## Checks
1. `uv sync`; `uv run ruff check .` and `uv run pytest` pass.
2. `uv run python scripts/check_keys.py` prints a table; missing keys show ❌ with a message, not a stack trace.
3. `uv run streamlit run app/main.py` starts; all 10 tabs render; Monetary Policy shows 5 sub-tabs.
4. First commit: "Skeleton: config, storage, source stubs, placeholder tabs".
