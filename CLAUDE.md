# Chile Rates Monitor — CLAUDE.md

## What this is
A personal web dashboard for an EM macro rates trader in NYC focused on Chile. It has two goals:
1. Monitor Chilean and global macro and rates markets in one place, refreshed on demand.
2. Provide trading tools: implied policy-rate path, UF carry, surprise indices, cross-market betas, auction supply.

**Hard rule: public data only.** Free APIs, public websites, open-source libraries. No Bloomberg or paid vendors.

## Stack
- Python 3.12, managed with **uv** (`uv add`, `uv run`).
- **Streamlit** front end, **Plotly** charts.
- **DuckDB** storage (`data/monitor.duckdb`). It may move to MotherDuck or Postgres later, so keep all DB access inside `src/monitor/storage.py`.
- **httpx** + **tenacity** for HTTP with retries; **BeautifulSoup/lxml** for HTML; **pdfplumber** for PDFs; **Playwright** only if a page truly needs JavaScript (ask first).
- **pandas / numpy / scipy / statsmodels** for analytics.
- **pydantic** for config models, **PyYAML** for the YAML registry, **python-dotenv** for `.env`.
- **pytest** + **ruff** for quality.
- Scheduling: GitHub Actions cron (later) or local `uv run python -m monitor.jobs.<job>`.

## Architecture
```
Sources (APIs, scrapers) → src/monitor/sources/*  (one module per source, returns tidy DataFrames)
        → src/monitor/storage.py                  (DuckDB upserts, the only DB access layer)
        → src/monitor/models/*                    (pure analytics: implied TPM, UF carry, surprises, betas)
        → app/                                    (Streamlit tabs read from storage + models, never call APIs directly
                                                   except through the Refresh action, which calls the jobs layer)
```
- `config/series.yaml` is the **series registry**: every time series the app uses, with source, source code, frequency, unit, tab and transform. Adding a series = editing YAML, not code. Entries carry `verified: true|false`; unverified codes must be confirmed before a job relies on them.
- `config/meetings.yaml`: BCCh and FOMC meeting dates (decision date, effective date).
- `config/sources.yaml`: base URLs, rate limits, scraper targets.
- The **Refresh** button on each tab triggers that tab's ingestion jobs, then reloads from DuckDB. Cache with `st.cache_data` using sensible TTLs.
- `src/monitor` is an installed package (src layout); `app/` imports it as `monitor`. App tab modules live in `app/tabs/` (the calendar tab is `calendar_tab.py` to avoid shadowing stdlib `calendar`).

## Data storage (DuckDB schema)
- `series_meta(series_id PK, source, source_code, name, freq, unit, tab, transform, notes)`
- `observations(series_id, obs_date, value, source, fetched_at)`. Upsert on (series_id, obs_date). Also append to `observations_history(series_id, obs_date, value, source, fetched_at)` whenever a value is new or changes, which keeps **data vintages** (needed for IMACEC/CPI revisions and surprise calculations).
- `market_snapshots(source, ticker, snapshot_ts, payload_json)`: Kalshi/Polymarket order-book and price snapshots.
- `documents(doc_id PK, kind, institution, published_at, title, url, raw_path, text, sha256)`: BCCh statements, minutes, IPoM, Hacienda calendars.
- `auctions(...)`: Treasury auction calendar and results (Bond Monitor, later). Provisional columns exist; finalize in the Bond Monitor spec.
- `ingest_log(job, source, started_at, finished_at, status, rows, error)`: drives the Data Health tab.
- Raw downloads (HTML, PDF, JSON) are saved under `data/raw/<source>/<date>/` so parsers can be re-run without re-fetching.

## Coding practices
- Type hints everywhere. Use pydantic models for config, and dataclasses or pydantic for parsed records.
- Source modules: one function per dataset, returning a tidy DataFrame `[series_id, obs_date, value]` or a typed record list. No Streamlit imports in `src/`.
- Models are **pure functions** (inputs → outputs), unit-tested, with no I/O.
- **Never fabricate or forward-fill data silently.** If a source fails, show the last stored value with a "stale since …" badge.
- Every chart and table shows **source + as-of timestamp**.
- Time zones: store UTC, display America/New_York, and keep America/Santiago for Chilean release and meeting times.
- Chilean formats: decimal comma (`4,50`), thousands dot, dates like `02.Ene.2026`. Use a single shared parser in `src/monitor/parsing.py`.
- Secrets live only in `.env` (see `.env.example`), loaded with python-dotenv. Never commit `.env` or print keys. The BCCh token travels as a URL query parameter, so **never log raw request URLs**: use `monitor.http.redact()` on anything that may contain a URL.
- HTTP: timeouts, retries with exponential backoff, and a descriptive User-Agent (all in `src/monitor/http.py`).
- Logging with the stdlib `logging` module. Every job writes to `ingest_log`.
- Tests: pytest. Source parsers are tested against **saved fixtures** in `tests/fixtures/`, not live calls. Mark live-API tests `@pytest.mark.live`.
- Keep functions small. Run `ruff check` and `ruff format` before finishing a task.

## Web scraping rules
- Prefer official APIs. Scrape only when no API exists.
- Check robots.txt. Rate-limit to at most 1 request per second per host, and cache raw responses.
- Use `httpx` + BeautifulSoup first, and Playwright only if the content is rendered by JavaScript.
- Parsers must fail loudly when the page structure changes: raise, log, and show the stale badge. Never return partial garbage.
- Store the original document (`data/raw/...`) plus a sha256 hash, so changes to documents like the Hacienda calendar can be detected and diffed.

## Data sources
| Source | Access | Key | Used for |
|---|---|---|---|
| BCCh Base de Datos Estadísticos (BDE) API | REST `https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx?token=…&function=GetSeries\|SearchSeries` (httpx, no wrapper lib) | `BCCH_API_TOKEN` (free, 1-year validity) | TPM, CPI, IMACEC, UF, USD/CLP, Cámara swaps (SPC), BCP/BTP/BCU/BTU secondary rates, EEE/EOF surveys, derivatives positioning |
| BCCh website (bcentral.cl) | scraping | none | Policy statements (comunicados), minutes (actas/minutas), IPoM, meeting calendar, board members, survey PDFs |
| BCCh Informativo Diario (daily PDF) | pdfplumber | none | T-1 SPC and bond rates, deposit rates, BCCh/Treasury auction results, FX market flows |
| Kalshi Trade API v2 | REST, public market data | none for reads | BCCh decisions (series `KXCBDCHILE`), Fed decisions, US CPI/NFP markets, candlestick history |
| Polymarket (Gamma API) | REST, public | none | Second prediction-market source (VERIFY endpoints) |
| FRED | REST | `FRED_API_KEY` (free) | UST yields, SOFR, CPI, PCE, NFP, GDP, breakevens, financial conditions |
| yfinance | Python lib (unofficial) | none | Copper (HG=F), Brent (BZ=F), WTI, DXY, VIX, ETFs; may break, so treat it as non-critical |
| Banxico SIE API | REST | `BANXICO_TOKEN` (free) | Mexico policy rate, Mbono yields (transmission panel) |
| BCB SGS (Brazil) | REST | none | Selic, Brazil macro (LatAm RV) |
| BanRep (Colombia) | VERIFY | VERIFY | TES yields, policy rate |
| Ministerio de Hacienda, Oficina de la Deuda Pública | scraping | none | Treasury auction calendar (bonds, dates, sizes), results |
| INE Chile | scraping | none | Release calendar (CPI, labor) |
| US Treasury FiscalData | REST | none | UST auction calendar (optional) |
| EIA | REST | `EIA_API_KEY` (free, optional) | Oil inventories (Thematic) |
| Anthropic API | `anthropic` SDK | `ANTHROPIC_API_KEY` (paid, optional) | Hawk/dove scoring, statement summaries; features must degrade gracefully without the key |

BDE REST notes: request dates `firstdate`/`lastdate` are `YYYY-MM-DD`; response `Obs[].indexDateString` is `DD-MM-YYYY`, `value` is a string (`"NaN"` with `statusCode: "ND"` = missing, drop it, never fill). SearchSeries needs `frequency` (DAILY, MONTHLY, QUARTERLY, ANNUAL) and returns `SeriesInfos[]`. Responses are `charset=ISO-8859-1`: parse with `json.loads(resp.text)`, never `resp.json()`.

## Tabs (status: placeholder unless noted)
1. **Monetary Policy** (sub-tabs: Overview · Board & Communication · Decision History · Implied Path & Probabilities · Surveys)
   - Board members and tenures, statement archive with a diff viewer vs the previous statement, minutes, vote record and dissents. Source: bcentral.cl scraping.
   - Hawk/dove score per statement and minutes (Anthropic API, optional).
   - Decision history with surprise vs EEE/EOF consensus. Source: BDE (TPM, surveys) + meetings.yaml.
   - Implied TPM path from Cámara swaps (BDE, T-1 close) + Kalshi `KXCBDCHILE` (live) + Polymarket, overlaid with EEE/EOF medians and the IPoM TPM corridor.
2. **Chile Macro**: CPI headline, core (IPC SAE), goods/services; IMACEC (mining vs non-mining, 3m/3m SAAR); labor; trade; USD/CLP dólar observado. Tools: **UF carry calculator**, **CPI seasonality/nowcast table**, **surprise index** (actual minus EEE median), data vintages. Source: BDE API; INE calendar.
3. **Curves & Rates**: nominal BCP/BTP and real BCU/BTU curves, breakevens, SPC curve (nominal and UF), 1d/1w/1m changes, carry/rolldown, spread vs UST. Source: BDE (daily, T-1, transaction-based with gaps) + Informativo Diario; FRED for UST.
4. **FX & Flows**: USD/CLP, BCCh FX operations, formal FX market flows, non-resident SPC positioning (BDE daily derivatives tables), AFP flows (Superintendencia de Pensiones, monthly, VERIFY). Source: BDE, Informativo Diario.
5. **Global Macro**: US rates and data, Fed probabilities, **transmission panel** (rolling betas of Chile / Mexico / Colombia rates to UST 2y/10y, DXY, copper; residual "idiosyncratic" move; inspired by Dirk Willer, *Trading Global Macro Markets*). Source: FRED, Kalshi, yfinance, BDE, Banxico SIE, BanRep.
6. **Calendar**: BCCh meetings, IPoM, INE releases, Hacienda auctions, US releases, FOMC; each with consensus and last surprise. Source: meetings.yaml, INE/BCCh/Hacienda scraping, FRED release dates.
7. **Bond Monitor** (pinned, later): Hacienda auction calendar auto-scraped (bond, date, size), auction results (bid-to-cover, allocation by banks/AFPs/others), DV01 per auction computed from the bond master (coupon, maturity from the nemotécnico) and BDE yields, weekly DV01 supply. Source: Hacienda ODP pages, Informativo Diario, BDE.
8. **Copper**: COMEX price, copper–CLP rolling beta, China indicators, Cochilco production (VERIFY access). Source: yfinance, BDE, Cochilco.
9. **Thematic**: config-driven theme cards (`config/themes/*.yaml`): Oil shock / Middle East, AI capex borrowing, etc. Source: yfinance, FRED, EIA, plus Claude-written summaries (optional).
10. **Data Health** (implemented): last successful fetch per source and series, staleness vs expected frequency, recent errors (from `ingest_log`).

## Key domain notes
- TPM = Tasa de Política Monetaria. The BCCh board has 5 members and holds 8 monetary policy meetings (RPM) per year. Statements come out the evening of the decision; minutes about 2 weeks later.
- SPC = Swap Promedio Cámara (OIS on the Cámara index, which tracks the overnight interbank rate ≈ TPM). In the BDE, 90/180/360d traded rates are on a **360-day simple basis and T-1**. The 1y–10y tenor table is **365-day basis, closing data**. Harmonize day-count bases before modeling.
- UF = Unidad de Fomento, an inflation-indexed unit; its daily path for the next month is fixed by the latest CPI print.
- BCCh CPI reference series is in **base 2023=100** since Jan 2024. Old base-2018 codes return no recent data. Official INE headline: `F074.IPC.IND.Z.2023.C.M`; BCCh-spliced analytics family (SAE, volátiles, goods/services): `G073.*.2023.M`.
- IMACEC is still on national-accounts **reference 2018** (`F032.IMC.IND.Z.Z.EP18.*`).
- EEE = Encuesta de Expectativas Económicas (monthly, includes TPM at next/subsequent meeting, 5m, 11m, 23m). EOF = Encuesta de Operadores Financieros (before and after each RPM).

## How to work in this repo
- Specs for each feature live in `docs/specs/NN-*.md`. Implement one spec per session.
- Before coding: read the spec, list files you will touch, confirm data sources and series codes (use `monitor.sources.bcch.search_series(frequency, query)` — BDE SearchSeries — to find BDE codes; never guess).
- After coding: tests pass, ruff clean, the app runs (`uv run streamlit run app/main.py`), CLAUDE.md updated if architecture changed.
