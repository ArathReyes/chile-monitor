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
- **holidays** (country CL, categories public + bank) for the Chilean business-day calendar (`src/monitor/cl_calendar.py`).
- **pytest** + **ruff** for quality.
- **No paid APIs.** The Anthropic API is not used (decision 2026-11-14): no LLM scoring or summaries.
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
- `config/meetings.yaml`: BCCh and FOMC meeting dates (past BCCh meetings regenerated from statements) (`meeting_id`, `decision_date`, optional `effective_date` — defaults to the next CL business day — `has_ipom`, `source`, `confirmed`). Entries sourced from prediction-market rules stay `confirmed: false` until checked against the BCCh calendar.
- `config/sources.yaml`: base URLs, rate limits, scraper targets.
- The **Refresh** button on each tab triggers that tab's ingestion jobs, then reloads from DuckDB. Cache with `st.cache_data` using sensible TTLs.
- `src/monitor` is an installed package (src layout); `app/` imports it as `monitor`. App tab modules live in `app/tabs/` (the calendar tab is `calendar_tab.py` to avoid shadowing stdlib `calendar`).

## Data storage (DuckDB schema)
- `series_meta(series_id PK, source, source_code, name, freq, unit, tab, transform, notes)`
- `observations(series_id, obs_date, value, source, fetched_at)`. Upsert on (series_id, obs_date). Also append to `observations_history(series_id, obs_date, value, source, fetched_at)` whenever a value is new or changes, which keeps **data vintages** (needed for IMACEC/CPI revisions and surprise calculations).
- `market_snapshots(source, ticker, snapshot_ts, payload_json)`: Kalshi/Polymarket outcome snapshots (`source` kalshi / polymarket, ticker = event, payload `{"outcomes": [...]}`) and Kalshi daily candles (`source` kalshi_candles, ticker = market).
- `documents(doc_id PK, kind, institution, meeting_id, published_at, title, url, raw_path, text, sha256)`: BCCh statements, minutes, IPoM, Hacienda calendars.
- `survey_distribution(survey, survey_month, section_no, section, horizon, median, p10, p90, n, doc_sha256)`: EEE results-PDF summary table (deciles, respondent counts, next/following-meeting TPM).
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
- Schema changes to existing tables go in `storage.MIGRATIONS` (idempotent `ALTER ... IF NOT EXISTS`), since `CREATE TABLE IF NOT EXISTS` won't alter a live database.
- DuckDB 2.x: never use `fetchone()` (it leaves the result open and blocks the next statement on the connection); use `storage._first()` / `fetchall()`. DuckDB returns DATE columns to pandas as `datetime64`.

## Web scraping rules
- Prefer official APIs. Scrape only when no API exists.
- Check robots.txt. Rate-limit to at most 1 request per second per host, and cache raw responses.
- Use `httpx` + BeautifulSoup first, and Playwright only if the content is rendered by JavaScript.
- Parsers must fail loudly when the page structure changes: raise, log, and show the stale badge. Never return partial garbage.
- Store the original document (`data/raw/...`) plus a sha256 hash, so changes to documents like the Hacienda calendar can be detected and diffed.
- **Never work around bot protection** (challenge pages, CAPTCHAs, WAFs). If a site blocks automated access, switch to a manual-download workflow.
- **www.bcentral.cl:** HTML pages are behind Imperva Incapsula and robots.txt disallows `/*reunion-de-politica-monetaria-rpm*`, `/*buscador*`, `/*search*` (and the new-search variants). Pages are saved manually into `data/manual/bcch_web/<kind>/` (see `docs/manual-data.md`); `sources/bcch_web.py` extracts `/documents/` links from them and downloads those files (allowed, served normally).

## Data sources
| Source | Access | Key | Used for |
|---|---|---|---|
| BCCh Base de Datos Estadísticos (BDE) API | REST `https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx?token=…&function=GetSeries\|SearchSeries` (httpx, no wrapper lib) | `BCCH_API_TOKEN` (free, 1-year validity) | TPM, CPI, IMACEC, UF, USD/CLP, Cámara swaps (SPC), BCP/BTP/BCU/BTU secondary rates, EEE/EOF surveys, derivatives positioning |
| BCCh website (bcentral.cl) | **manual save** of pages/PDFs → `bcch_web.py` parses links, downloads `/documents/` files | none | Policy statements, minutes, IPoM, meeting calendar, board members, EEE results PDFs |
| BCCh Informativo Diario (daily PDF) | pdfplumber | none | T-1 SPC and bond rates, deposit rates, BCCh/Treasury auction results, FX market flows |
| Kalshi Trade API v2 | REST `https://external-api.kalshi.com/trade-api/v2` (fallback `api.elections.kalshi.com`), public | none for reads | BCCh decisions (`KXCBDCHILE`; outcomes C25P/C25/HOLD/H25/H25P, dollar-string prices), candlesticks `/series/{s}/markets/{m}/candlesticks` |
| Polymarket (Gamma API) | REST `https://gamma-api.polymarket.com` `/public-search?q=`, `/events?slug=` (verified 2026-11-14) | none | BCCh decision events `central-bank-of-chile-decision-in-<month>-<year>`; outcomes by `groupItemTitle`; often illiquid (quality flag) |
| FRED | REST | `FRED_API_KEY` (free) | UST yields, SOFR, CPI, PCE, NFP, GDP, breakevens, financial conditions |
| yfinance | Python lib (unofficial) | none | Copper (HG=F), Brent (BZ=F), WTI, DXY, VIX, ETFs; may break, so treat it as non-critical |
| Banxico SIE API | REST | `BANXICO_TOKEN` (free) | Mexico policy rate, Mbono yields (transmission panel) |
| BCB SGS (Brazil) | REST | none | Selic, Brazil macro (LatAm RV) |
| BanRep (Colombia) | VERIFY | VERIFY | TES yields, policy rate |
| Ministerio de Hacienda, Oficina de la Deuda Pública | scraping | none | Treasury auction calendar (bonds, dates, sizes), results |
| INE Chile | scraping | none | Release calendar (CPI, labor) |
| US Treasury FiscalData | REST | none | UST auction calendar (optional) |
| EIA | REST | `EIA_API_KEY` (free, optional) | Oil inventories (Thematic) |

BDE REST notes: request dates `firstdate`/`lastdate` are `YYYY-MM-DD`; response `Obs[].indexDateString` is `DD-MM-YYYY`, `value` is a string (`"NaN"` with `statusCode: "ND"` = missing, drop it, never fill). SearchSeries needs `frequency` (DAILY, MONTHLY, QUARTERLY, ANNUAL) and returns `SeriesInfos[]`. Responses are `charset=ISO-8859-1`: parse with `json.loads(resp.text)`, never `resp.json()`.

## Tabs (status: placeholder unless noted)
1. **Monetary Policy** (implemented, spec 01; sub-tabs: Overview · Board & Communication · Decision History · Implied Path & Probabilities · Surveys). Code: `app/tabs/monetary_policy.py`, `app/tabs/mp_*.py`, data layer `app/mp_data.py`, models `models/tpm_implied.py`, `models/surprises.py`, `models/prediction_markets.py`, `models/text_analysis.py`; jobs `jobs/mp_daily.py`, `jobs/bcch_series.py`, `jobs/snapshot_markets.py` (hourly).
   - Implemented: Overview KPIs, implied path (SPC + EOF prior + Kalshi anchor) with probabilities and diagnostics, Kalshi/Polymarket panels, decision history (from TPM changes + meetings.yaml) with EOF/EEE surprises, EEE/EOF survey charts, EEE decile table once PDFs are saved.
   - Statements: 45 English press releases May 2021 – Oct 2026 (complete set, saved manually, standardized names). Parsed by `models/statements.py` (date, decision, vote, dissents; all 45 decisions match BDE TPM changes). `models/board.py` gives the vote record and each member's revealed stance from dissents (gap = preferred − decided bp). `scripts/sync_meetings_from_statements.py` rebuilds past meetings in meetings.yaml.
   - Board: `sources/board.py` parses the official "Histórico Autoridades" PDF (governors, vice-governors, board members, years; open-ended rows = current) and saved profile pages (name, title, bio, education sentences). Cards join this with the statement-based stance (`models/board.py`: current_board, attach_stance, former_in_statements). `config/board_overrides.yaml` holds confirmed name aliases only. Photos: bcentral.cl blocks image downloads (even browser saves store a challenge page); photos saved separately in any `board/` subfolder (currently `Board-Pictures/`), matched by name words in the file name, validated by magic bytes.
   - Statement hawk/dove = EOF tone `eof_statement_bias` (F089.EOF.PS_TPM.CMA.D): 0 most dovish, 5 neutral, 10 most hawkish (scale from the EOF questionnaire, user-provided 2026-11-14); one reading per meeting in the round 15–22 days after it, mapped by `models/board.statement_tone`. No LLM scoring.
   - Profiles match the authorities list by name tokens (`sources/board.match_profile`; pages omit the maternal surname).
   - Pending: the Griffith-Jones vs "Stepanka Novy Kafka" naming conflict (statements vs authorities PDF), minutes (optional), IPoM corridor. 
2. **Chile Macro** (implemented, spec 02; 7 sub-tabs: Overview · Inflation · Seasonality & Nowcast · UF & Carry · Activity · Labor & External · Surprises & Calendar). Code: `app/tabs/chile_macro.py`, `app/tabs/cm_*.py`, data layer `app/cm_data.py`; models `models/uf.py`, `seasonality.py`, `inflation_nowcast.py`, `macro_surprises.py`, `transforms.py`; sources `sources/ine.py`; job `jobs/macro_daily.py` (BDE `chile_macro` + `curves_rates` series, INE calendar, recompute `release_surprises`).
   - **CPI splice:** only official splices are used — BCCh `G073.*.2023.M` (headline from 1998, sin volátiles, volatiles) and INE `F074.*.EP23.*` (core SAE, goods/services, tradables from 2009); official m/m history from 1928 `F074.IPC.VAR.Z.Z.C.M` (rounded, used for seasonality, surprises and the UF). No index levels are mixed across bases.
   - **CPI weights:** `config/cpi_weights.yaml`, recovered from the fixed-weight structure (headline = Σ wᵢ·divisionᵢ, 13 divisions, base 2023; max residual 0.006 index pts) by `scripts/estimate_cpi_weights.py`. Replace with INE's table if obtained.
   - **UF formula:** from the 10th of month t to the 9th of t+1, UF = round(UF₉ × (1+π_{t−1})^(k/N), 2), π = INE published m/m. Verified exactly on every BDE daily UF value since 2012 (177 periods); that check is the citation (no official doc consulted). The BDE publishes the UF ahead (to the 9th of next month), so jobs never pass a `lastdate`.
   - **Market-implied monthly CPI: not available.** No public short-end UF swaps (BDE UF tenor table starts at 1y; Informativo Diario has only nominal SPC 90/180/360d). User decision (2026-11-15): don't model or interpolate it. Market view = BCCh-published breakevens (`cl_bei_*`: swaps spot 1/2/5/10y, fwd 1y1y/3y2y/5y5y; bonds spot 2/5/10y, fwd 3y2y/5y5y).
   - **Surprise index:** surprise = actual − EEE median (CPI m/m: survey month m "en el mes" → CPI of m; IMACEC y/y: survey month m "un mes atrás" → month m−1). z = surprise / std of earlier surprises (expanding, ≥12). Index = Σ zᵢ·0.5^(age/half-life), half-life 3 months default; inflation (CPI) and activity (IMACEC, non-mining) sub-indices. IMACEC uses revised data (no historical vintages; first prints accumulate from now). No unemployment consensus in the BDE.
   - **Calendar:** INE "agenda estadística" scraped (CPI, employment, wages). BCCh statistical calendar on hold (bot-protected site).
   - USD/CLP vs copper (LME, BDE `F019.PPB.PRE.100.D`) shown as two aligned panels (no dual axes); ICMO real is computed (nominal deflated by CPI).
3. **Curves & Rates**: nominal BCP/BTP and real BCU/BTU curves, breakevens, SPC curve (nominal and UF), 1d/1w/1m changes, carry/rolldown, spread vs UST. Source: BDE (daily, T-1, transaction-based with gaps) + Informativo Diario; FRED for UST.
4. **FX & Flows**: USD/CLP, BCCh FX operations, formal FX market flows, non-resident SPC positioning (BDE daily derivatives tables), AFP flows (Superintendencia de Pensiones, monthly, VERIFY). Source: BDE, Informativo Diario.
5. **Global Macro**: US rates and data, Fed probabilities, **transmission panel** (rolling betas of Chile / Mexico / Colombia rates to UST 2y/10y, DXY, copper; residual "idiosyncratic" move; inspired by Dirk Willer, *Trading Global Macro Markets*). Source: FRED, Kalshi, yfinance, BDE, Banxico SIE, BanRep.
6. **Calendar**: BCCh meetings, IPoM, INE releases, Hacienda auctions, US releases, FOMC; each with consensus and last surprise. Source: meetings.yaml, INE/BCCh/Hacienda scraping, FRED release dates.
7. **Bond Monitor** (pinned, later): Hacienda auction calendar auto-scraped (bond, date, size), auction results (bid-to-cover, allocation by banks/AFPs/others), DV01 per auction computed from the bond master (coupon, maturity from the nemotécnico) and BDE yields, weekly DV01 supply. Source: Hacienda ODP pages, Informativo Diario, BDE.
8. **Copper**: COMEX price, copper–CLP rolling beta, China indicators, Cochilco production (VERIFY access). Source: yfinance, BDE, Cochilco.
9. **Thematic**: config-driven theme cards (`config/themes/*.yaml`): Oil shock / Middle East, AI capex borrowing, etc. Source: yfinance, FRED, EIA.
10. **Data Health** (implemented): last successful fetch per source and series, staleness vs expected frequency, recent errors (from `ingest_log`).

## Key domain notes
- TPM = Tasa de Política Monetaria. The BCCh board has 5 members and holds 8 monetary policy meetings (RPM) per year. Statements come out the evening of the decision; minutes about 2 weeks later.
- SPC = Swap Promedio Cámara (OIS on the Cámara index, which tracks the overnight interbank rate ≈ TPM). In the BDE, 90/180/360d traded rates are on a **360-day simple basis and T-1**. The 1y–10y tenor table is **365-day basis, closing data**. Harmonize day-count bases before modeling.
- UF = Unidad de Fomento, an inflation-indexed unit; its daily path for the next month is fixed by the latest CPI print.
- BCCh CPI reference series is in **base 2023=100** since Jan 2024. Old base-2018 codes return no recent data. Official INE headline: `F074.IPC.IND.Z.2023.C.M`; BCCh-spliced analytics family (SAE, volátiles, goods/services): `G073.*.2023.M`.
- IMACEC is still on national-accounts **reference 2018** (`F032.IMC.IND.Z.Z.EP18.*`).
- EEE = Encuesta de Expectativas Económicas (monthly). BDE series `F089.TPM.TAS.*`: this month (11), next month (31), 5m (26), 11m (14), 17m (30), 23m (15), 35m, long run (LP), Dec-of-year (2026, 2027); **no next-meeting series in the BDE** — the "siguiente / subsiguiente reunión" horizons and deciles are only in the results PDF (`sources/eee_pdf.py`).
- EOF = Encuesta de Operadores Financieros, ~2 rounds per meeting cycle, BDE `F089.EOF.TPM.*` dated by survey date: MA (próxima RPM), 1REU, 3MS (2 meetings ahead), 3REU, 6MS (4 ahead), 12MS, 24MS. Pre-round "1REU" and post-round "MA" target the same next meeting.
- TIB (overnight interbank average) `F022.TIB.TIP.D001.NO.Z.D`; core CPI `F074.IPCSAE.IND.Z.2023.C.M` (spliced from 2009: `F074.IPCSAE.IND.Z.EP23.Z.M`).

## Implied-path model assumptions (models/tpm_implied.py)
- TPM steps on effective dates = decision + 1 CL business day; overnight = TPM + spread (spread = 20-day average TIB − TPM).
- Cámara index compounds business-day rates over calendar days, ACT/360. Swaps ≤ 1y: single payment, simple ACT/360. Swaps > 1y: **semiannual coupons, ACT/360** (confirmed by the user 2026-11-14), discounted on the same curve.
- BDE tenor-table quotes (365 basis) are converted ×360/365; traded 90/180/360d are already 360. Swaps settle **T+2** CL business days (`cl_calendar.spc_start`, confirmed by the user 2026-11-15).
- EOF prior (`tpm_implied.latest_eof_round` + `eof_prior`, shared by app and backtest): all keys from the single latest round; meeting positions shift by the meetings held between the survey date and the first step (the round's "next" may already be past).
- Defaults: N = 8 steps (known meetings, padded with an unscheduled monthly grid, flagged), λ = 0.005 (EOF prior), μ = 0.05 (smoothness; was 0.25 — lowest meeting-1 MAE and bias at h = 1/5/20 bd in `docs/backtests/01-implied-path.md`), Kalshi anchor on meeting 1 (weight 4, only when liquid), 2y off by default (flat tail after the last step makes it a weak constraint). With 3 short swaps for 8 unknowns, later meetings lean on prior/smoothness — the UI says so.

## How to work in this repo
- Specs for each feature live in `docs/specs/NN-*.md`. Implement one spec per session.
- Before coding: read the spec, list files you will touch, confirm data sources and series codes (use `monitor.sources.bcch.search_series(frequency, query)` — BDE SearchSeries — to find BDE codes; never guess).
- After coding: tests pass, ruff clean, the app runs (`uv run streamlit run app/main.py`), CLAUDE.md updated if architecture changed.
