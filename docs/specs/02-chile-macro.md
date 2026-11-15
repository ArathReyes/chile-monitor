# Spec 02 — Chile Macro tab

Status: **implemented** (2026-11-15). Source: Prompt 3 (user).

## Decisions taken during implementation (user, 2026-11-15)
1. No market-implied monthly CPI: short-end UF swaps aren't public; don't model or guess them.
2. USD/CLP vs copper as two aligned panels (no dual axis); copper from the BDE (LME), not yfinance.
3. BCCh statistical release calendar on hold (bot-protected); INE calendar scraped.
4. No real-time vintage database: the UI states IMACEC surprises use revised data.
5. UF formula cited by exact reproduction of BDE history (no official doc available).
6. Real ICMO computed (nominal deflated by CPI), labeled.
Other: CPI weights recovered from the fixed-weight index (no INE table needed); 13 divisions (base 2023).

## Discovery notes (2026-11-15)
- BDE has no short-end UF Cámara swaps (90/180/360d); the UF tenor table starts at 1y. The
  Informativo Diario lists only nominal SPC 90/180/360d ("90 días UF" rows are UF deposit rates,
  empty). Month-by-month market-implied CPI needs an alternative method (see plan).
- BDE "compensación inflacionaria" exists for swaps (spot 1y/2y/5y/10y, fwd 1y1y/3y2y/5y5y) and
  bonds (spot 2y/5y/10y, fwd 3y2y/5y5y).
- CPI base 2023 has 13 divisions (COICOP 2018), not 12.
- www.bcentral.cl pages are bot-protected: BCCh statistical calendar and any real-time vintage
  database need manual saving. si3.bcentral.cl (BDE, Informativo Diario PDF) downloads normally.

---

Implement the **Chile Macro** tab described in CLAUDE.md. Read CLAUDE.md first and follow its coding practices, storage schema (including **vintages** in `observations_history`) and scraping rules.

**How to work:**
- First, show a plan: files, data sources, the series to search for, and open questions.
- Build in the **phases** below. After each phase, run the tests and the app, commit, and give a short report.
- Never guess BDE series codes. Discover them with `monitor.sources.bcch.search_series()` (replaces `siete.buscar()`), confirm frequency, unit and base year, and save sample responses to `tests/fixtures/`. If a series can't be found, stop and report. Don't substitute a different one silently.
- Reuse and extend `bcch.py`, `meetings.yaml` and the EEE ingestion from spec 01. Don't duplicate.

The tab gets **7 sub-tabs**: Overview · Inflation · Seasonality & Nowcast · UF & Carry · Activity · Labor & External · Surprises & Calendar.

## Phase 1 — Data foundations

### 1a. BDE series (`src/monitor/sources/bcch.py`)
Find, verify and register in `config/series.yaml` with `tab: chile_macro`:
- **Inflation (monthly):** CPI index base 2023=100 headline (m/m, y/y); core CPI (IPC SAE) and CPI ex food and energy if available; goods vs services, tradables vs non-tradables, volatile; the CPI divisions for a contributions chart (weights from the INE methodology doc into `config/cpi_weights.yaml` with a source note if not in the BDE). **Base change:** the reference CPI moved to base 2023=100 in Jan 2024 — use the official splice/chained series if one exists, otherwise splice on monthly % changes and document it. Never mix index levels across bases.
- **UF:** daily value.
- **Activity:** IMACEC headline, mining, non-mining (original and SA); sector breakdown (goods, commerce, services) if available; GDP quarterly (y/y, q/q SA); consumption and investment nice-to-have.
- **Labor:** unemployment rate (moving quarter), employment, labor force; wages: nominal wage index (IR) and labor cost index (ICMO), real versions if available.
- **External:** exports (total, copper), imports, trade balance (monthly); current account (quarterly, % of GDP); USD/CLP dólar observado (daily) as context.
- **Expectations (EEE):** CPI "en el mes", "mes siguiente", 11m, 23m, 35m, December of current and next year; IMACEC expectation for the month, GDP current and next year (VERIFY). **Real-time first prints:** for each EEE survey date, store which reference month each answer applies to.
- **Market-implied inflation:** BDE "compensación inflacionaria" from Cámara swaps (spot 1y, 2y, forwards e.g. 3y in 2y) and bond-based breakevens (BCP − BCU at matching tenors), if published; Cámara swap UF vs nominal at the short end (90d/180d/360d) for the market-implied monthly CPI.

### 1b. Release calendar (`src/monitor/sources/ine.py`, `bcch_web.py`)
- Scrape the INE release calendar (CPI, labor) and the BCCh statistical release calendar (IMACEC, GDP, trade, current account) for the current and next year.
- Store in `config/release_calendar.yaml` or a `releases` table: `indicator, reference_period, release_datetime_local (America/Santiago), source_url`. Fallback: manual YAML.

### 1c. Vintages and first prints
- Every ingestion writes changed values to `observations_history`, so first prints accumulate from now on.
- Historical first prints: check whether the BCCh publishes a real-time (vintage) database for IMACEC/GDP (VERIFY). If so, ingest it; if not, note in the UI that historical surprises use revised data until real vintages accumulate. CPI is essentially unrevised.

## Phase 2 — Models (`src/monitor/models/`), pure and unit-tested
- **2a `uf.py`:** UF rule — from the 10th of month t to the 9th of month t+1 the UF compounds daily, geometrically, so the total change equals the CPI change of month t−1 (exact formula in a docstring, citing the official methodology; VERIFY). `known_uf_path(cpi_mm_latest, uf_on_9th)`; `projected_uf_path(scenario)` under EEE median, seasonal average, market-implied, user override; `uf_carry(...)` for [start, end] and notional: UF accrual (% and bp, annualized and period), real vs nominal carry difference (BTU/BCU or SPC UF vs BTP/BCP or SPC CLP; inputs real yield, nominal yield, holding period, CPI scenario), breakeven monthly CPI. Tests: a historical month reproduces published daily UF within 0.01 CLP; zero CPI gives flat UF; carry with identical inputs is symmetric.
- **2b `seasonality.py`:** monthly % change history (spliced); per calendar month mean, median, min, max, std over a configurable window (default 10y), excluding outlier years (default 2021–2022, toggle in UI); same for core. Test: a synthetic seasonal series is recovered exactly.
- **2c `inflation_nowcast.py`:** for the current and next two months: seasonal average · last year's same month · EEE median · market-implied · (later) user estimate. Market-implied monthly CPI from the short-end SPC UF vs nominal spread using the known UF window (method and limits documented; "n/a" if too sparse/noisy). 12-month inflation implied by each scenario through December and 11 months ahead vs the 3% target and 2–4% band.
- **2d `surprises.py` (extend):** surprise = first print − EEE median before release, in the indicator's units; z-score by historical surprise std; **Chile Macro Surprise Index** = exponentially weighted sum of recent z-scores (half-life default 3 months; positive = stronger activity / higher inflation), with inflation and activity sub-indices; store in a `release_surprises` table. Tests: known example; decay behaves as expected.
- **2e `transforms.py`:** m/m, y/y, 3m/3m SAAR, 6m/6m SAAR, contributions to y/y (weight × component y/y, with normalization notes), moving averages. Unit-tested.

## Phase 3 — UI (`app/tabs/chile_macro.py` + `app/tabs/cm_*.py`)
Every chart/table shows source + as-of and a stale badge; each sub-tab has a Refresh that runs only its jobs; the latest print is always shown next to its consensus and surprise.
1. **Overview:** KPI cards (CPI m/m and y/y, core y/y, IMACEC y/y, non-mining IMACEC y/y, unemployment, UF today, USD/CLP) with last value, change, EEE consensus, surprise, next release date; mini charts (CPI and core vs 2–4% band; IMACEC y/y); small Surprise Index; next 2 weeks of releases with consensus.
2. **Inflation:** headline vs core y/y with band (long spliced history); m/m bars with core m/m; goods vs services and tradables vs non-tradables y/y; contributions to y/y by division (if weights); breakevens panel (SPC 1y, 2y, 3y-in-2y; BCP−BCU) vs EEE 11m/23m.
3. **Seasonality & Nowcast:** heatmap of CPI m/m (year × month); seasonal profile (mean/median, min–max band, current year overlaid; outlier toggle, window slider); nowcast table for current + next 2 months (seasonal · last year · EEE · market-implied · implied y/y; highlight disagreements > 10bp); 12-month path to December and 11 months ahead under each scenario.
4. **UF & Carry:** UF calendar (known window + projected, scenario selectable); carry calculator (instrument pair BTU/BCU vs BTP/BCP or SPC UF vs SPC CLP; tenor; real/nominal yields defaulting to latest BDE, editable; holding period defaulting to end of known UF window; notional CLP or UF) → UF accrual (bp), real vs nominal carry difference (bp and CLP), breakeven monthly CPI, sensitivity table (carry vs CPI m/m ±10/20bp); monthly UF accrual history.
5. **Activity:** IMACEC y/y (headline, mining, non-mining); IMACEC SA m/m and 3m/3m SAAR; sector breakdown; GDP y/y and q/q SA (components if available); EEE IMACEC/GDP vs actual.
6. **Labor & External:** unemployment with employment y/y; nominal and real wage growth (IR, ICMO); trade (copper vs non-copper exports, imports, 12m rolling balance); current account % of GDP; USD/CLP vs copper as context.
7. **Surprises & Calendar:** Surprise Index (inflation, activity) over time; release-by-release table (date, indicator, reference period, consensus, first print, surprise, z-score, revised value); surprise distribution per indicator and consensus hit rate; release calendar (next 60 days, Santiago and NY times, consensus, last surprise); data-quality note on IMACEC historical surprises.

## Phase 4 — Jobs, tests, docs
- `monitor.jobs.macro_daily`: BDE macro series, UF, breakevens, EEE, release calendars; on release days run again shortly after the scheduled release time.
- All jobs write to `ingest_log`; every new series appears in Data Health with its expected frequency.
- Tests: parsers vs fixtures (BDE, INE/BCCh calendar HTML); UF reproduction vs real published values; seasonality, surprise, transform unit tests; smoke test rendering each sub-tab with fixture data.
- Update CLAUDE.md: Chile Macro implemented, verified codes, CPI splice method, UF formula and source, market-implied CPI method and limits, surprise index definition.
- Final report: done / VERIFY / blocked, each sub-tab, suggested next spec.

## References
- BCCh BDE API docs: https://si3.bcentral.cl/estadisticas/Principal1/Web_Services/documentacion.html
- BCCh API help (CPI base 2023 note): https://si3.bcentral.cl/estadisticas/Principal1/Web_Services/ayuda_soporte.html
- BCCh compensación inflacionaria basket: https://si3.bcentral.cl/siete/ES/Siete/Canasta?idCanasta=7UE452181
- EEE calendar: https://www.bcentral.cl/contenido/-/detalle/encuestas-de-expectativas-2026
- EEE results example: https://www.bcentral.cl/documents/33528/8691614/Gr%C3%A1ficos+%28distribuci%C3%B3n+de+respuestas%29+octubre+2026.pdf
- INE Chile: https://www.ine.gob.cl
- Estudio Económico Estadístico N°56 (Cámara swaps & Seguro Inflación), bcentral.cl
