# Spec 04 — Bond Monitor tab

Status: **in progress** (2026-11-15). Source: Prompt 5 (user). Runs after spec 03 (Curves & Rates).

## Decisions taken during implementation (user, 2026-11-15)
1. The provided `hacienda.py` / `test_hacienda.py` (in `scripts/`) were adapted into `src/monitor/sources/hacienda.py` and `tests/test_hacienda.py`. The originals stay in `scripts/` (untracked).
2. `openpyxl` added (reads the consolidated-results and Stock Total .xlsx files).
3. **No DV01 (or duration / pricing):** supply and auction sizes are shown in nominal terms only (native units, CLP and USD). "Bond Master & DV01" becomes "Bond Master"; the weekly supply chart is nominal.
4. launchd jobs installed on the user's Mac (Phase 4), explained afterwards.

## VERIFY outcomes (2026-11-15)
- Consolidated results: **.xlsx, one per year 2021–2026** (header row position and columns vary by year; `desierta` = void auction; Hacienda typos handled: 19xx/20xx century, 2033 auction dates in the 2023 file, a 2020 maturity for BTP0470930 in 2024 — each fix noted in `auction_results.note`). Extra sheets (2022 "Recompras", 2024 "Detalle Book building") not loaded yet.
- Bond master: **Stock Total .xlsx** (live bonds: ISIN, nemo, issue, maturity, coupon, outstanding) + **Características Financieras HTML** (coupon months; semiannual coupons). Nemo rule validated on every local bond in both.
- SOMA text: authorized USD 23,600mm for 2026, placed USD 21,563mm (15-Nov-2026).

---

Implement the **Bond Monitor** tab described in CLAUDE.md. Read CLAUDE.md first and follow its coding practices, storage rules and scraping rules, including the **runtime-independence rule**.

**How to work:**
- First, show me a plan: files, tables, open questions.
- Build in phases. After each phase, run the tests and the app, commit, and report.
- Never guess URLs, table layouts or file formats. Fetch the real pages, save them as fixtures, and build parsers against those fixtures.

The tab gets **5 sub-tabs**: Upcoming Auctions · Auction Results · Supply & Issuance · Bond Master & DV01 · Auction Impact.

---

## 0. Design principles for this tab (read before coding)

### 0.1 Scrapers are code, not Claude
Every scraper and calculation in this tab is plain Python in the repo. It runs unattended from the scheduler (cron/launchd/Task Scheduler or GitHub Actions) and from the dashboard's Refresh button. **No step may require a Claude session to keep the data current.** Claude Code builds and tests the scrapers; after that they run on their own. If a page layout changes, the parser must fail loudly: raise, write the error to `ingest_log`, and show a stale badge in the UI. It must never store partial or garbage data.

### 0.2 Where the data lives (verified on 2026-11-15; none of it is in the BCCh BDE API)
| Need | Source | Format | Notes |
|---|---|---|---|
| Auction calendar | Hacienda, Oficina de la Deuda Pública (ODP), "Calendario de Licitaciones" | **HTML table** (current quarter) | Columns grouped by Letras / BTP / BTU, with units (Millones de Pesos / Miles de UF), instrument names, ISIN and nemo in stacked header rows; rows = dates (`05-nov-26`); "(R)" = reopening. The page says the schedule may change, so **diff it on every run**. |
| Latest auction result | Hacienda ODP, "Resultados Última Licitación" (also on the "Colocaciones BCCh (SOMA)" page) | HTML table | Fields: Fecha de Licitación, Bono, Nemo, Fecha de Vencimiento, Unidad, Cupo, Monto Demandado, Total Adjudicado, Bancos, AFP/Cías. Seguros/Corredoras, Tasa de Interés. **Only the most recent auction is shown.** |
| Historical results (backfill) | Hacienda ODP "Estadísticas" → "Resultados Licitaciones (consolidados)" | **VERIFY** (Excel or PDF) | |
| Bond master (coupon, maturity, nemo, ISIN) | Hacienda ODP "Estadísticas" → "Características Financieras – Bonos Locales" | **VERIFY** | |
| Outstanding per bond | Hacienda ODP "Estadísticas" → "Bonos de Tesorería – Stock Total" | **VERIFY** | |
| Issuance vs annual authorization | Hacienda SOMA page text ("Del endeudamiento autorizado … el monto acumulado asciende a …") | HTML | Parse both USD amounts. |
| Exchange / buyback operations | BCCh "Operaciones de Mercado Abierto – Agencia Fiscal" | PDF/XLSX (`Resultados Intercambio Fiscal DD-MM-YYYY.pdf`, `Condiciones Financieras Intercambio …`) | Optional (Phase 4). |
| Results by tenor (cross-check) | BCCh Informativo Diario (daily PDF), section "Ventas por licitación de títulos emitidos por la Tesorería" | PDF | Optional cross-check. |
| Yields for DV01 | Fitted curves from the Curves & Rates tab (`curve_fits`), from BDE benchmark tenors | DuckDB | No free per-bond quotes exist (see the Prompt 4 Phase 0 findings). |
| UF and USD/CLP for conversions | BDE (UF daily, dólar observado) | API | Already ingested. |

### 0.3 When auctions happen and when results appear
- Auction dates come **from the calendar, not a fixed weekday.** In Q4 2026 they fall on Tuesdays and Thursdays (5, 12, 17, 19, 24 Nov), but the scheduler must read the stored calendar rather than assume weekdays.
- Auction format (BCCh fiscal agency rules): Dutch auction, bidding **10:30–11:00 Santiago time**, settlement T+2, yield convention Act/365 with two decimals.
- Results are published **the same day** on Hacienda's results page; the exact time isn't stated. The BCCh Informativo Diario reports them the next morning.
- Santiago is 2h ahead of New York in the southern summer, and the gap changes with each country's daylight-saving dates. Work in America/Santiago internally and display both time zones.

### 0.4 Results must accumulate, and consulting them must refresh them
Because Hacienda shows only the latest auction:
1. **Append-only storage.** Every scrape upserts into `auction_results` keyed on (auction_date, nemo). Never delete rows. History grows from the first run, and the backfill fills in the past.
2. **Revisions.** If a stored result changes (Hacienda corrects a figure), update the row and log each changed field in `auction_results_revisions`.
3. **Refresh-on-consult.** The dashboard reads results through `get_results(con)`. Before returning, it checks for any calendar auction dated today or earlier with no stored result, and if there is one, it scrapes first. So opening the Auction Results sub-tab is always up to date, even if a scheduled run was missed.
4. **Auction-day watcher.** `hacienda.py watch` runs every weekday morning. If the stored calendar has no auction today it exits immediately. Otherwise it polls the results page every 15 minutes from 11:05 to 18:00 Santiago time until today's result is stored.
5. **Calendar change log.** New, resized, removed or reinstated auctions are recorded in `auction_calendar_changes` and shown in the UI.

### 0.5 Starting point: `hacienda.py`
A working scraper module is provided (`src/monitor/sources/hacienda.py` + `tests/test_hacienda.py`, 19 tests on synthetic HTML). It already has:
- Parsers: `parse_calendar`, `parse_results`
- Storage: `store_calendar`, `store_results`
- Helpers: `missing_result_dates`, `get_results`, `watch`
- Backfill: `backfill`, which finds and downloads the consolidated-results file
- A `debug` command
- Tables: `auction_calendar`, `auction_calendar_changes`, `auction_results`, `auction_results_revisions`, `hacienda_ingest_log`

**Your job:**
1. Run `python -m monitor.sources.hacienda debug` (or the equivalent) against the **live** pages. Save the real HTML as fixtures and fix the parsers to match the real layout. Keep the synthetic tests and add real-fixture tests.
2. Adapt the module to the repo conventions: use the shared `http.py` client and `storage.py` connection, write to the common `ingest_log` instead of `hacienda_ingest_log` (keep a migration), and add the jobs under `monitor.jobs`.
3. Keep the CLI working, so it can run from cron without the app.

---

## Phase 1 — Data

### 1a. Calendar and results (`sources/hacienda.py`)
Validate and adapt per 0.5. Add:
- `amount_clp` / `amount_uf` normalization (Letras and BTP are in MM CLP, BTU in thousands of UF), plus a USD equivalent using the dólar observado and the UF of the auction date.
- An `instrument_type` column: Letra / BTP / BTU; and `is_reopening`.

### 1b. Backfill (`hacienda.backfill`)
- Download "Resultados Licitaciones (consolidados)".
  - If it's Excel: inspect the layout, write `load_consolidated()` mapping to `auction_results` (`source='consolidated'`), and test it against the saved file.
  - If it's PDF: try pdfplumber table extraction. If it isn't reliable, stop and report.
- Live scrapes take priority. Consolidated rows never overwrite a row whose source is the live page.

### 1c. Bond master (`sources/hacienda.py` → `bond_master` table)
- Download "Características Financieras – Bonos Locales" and parse it into `bond_master(nemo PK, isin, instrument_type, currency ('CLP'|'UF'), coupon_pct, coupon_freq, issue_date, maturity_date, amortization ('bullet'|…), day_count, source, updated_at)`.
- **Nemo decoding (hypothesis, validate against the master file before relying on it):** e.g. `BTP0500335` → 5.0% coupon, Mar-2035; `BTU0190930` → 1.9%, Sep-2030; `BTU0281155` → 2.8%, Nov-2055 (3-digit coupon with one implied decimal, then MMYY). Implement `decode_nemo()` and a test comparing it with every row of the master. Use it only as a fallback for bonds missing from the master, and flag those rows.
- **Outstanding** ("Bonos de Tesorería – Stock Total"): load into `bond_outstanding(nemo, as_of, amount, unit)`.
- Add any bond seen in the calendar or results but missing from the master to a "needs review" list in the Data Health tab.

### 1d. Issuance vs authorization (`sources/hacienda.py`)
Parse from the SOMA page the annual authorized amount and the cumulative amount placed (both USD). Store them as series (`hacienda_auth_usd_{year}`, `hacienda_placed_usd_{year}`) with vintages, so progress can be charted through the year.

### 1e. Optional (Phase 4): BCCh fiscal agency exchange operations
Index the PDFs/XLSX on the BCCh Agencia Fiscal page into `documents` (kind=`fiscal_exchange`). Parse "Resultados Intercambio Fiscal" if feasible.

---

## Phase 2 — Models (`src/monitor/models/bonds.py`, pure functions, tested)

- **Cash flows:** `cashflows(bond, settle_date)` returns coupon and principal dates and amounts. Confirm the coupon frequency (expected to be semiannual) and the payment-date rules from the master or BCCh/Hacienda documentation; document the source.
- **Pricing:** Chilean convention (VERIFY from BCCh or Hacienda documentation and cite it in the docstring). Expected: PV of each flow discounted at `(1 + y)^(days/365)`, price as % of par, accrued interest. Fall back to street-standard yield-to-price and flag it if the local convention can't be confirmed.
- **Yield for each bond:** `y = fitted_curve(maturity)` from `curve_fits` (nominal curve for BTP/Letras, real curve for BTU) on the latest obs_date. For a bond auctioned on date d, also keep the clearing rate as the yield at d.
- **Risk:** modified duration, convexity, and **DV01** = (P(y−1bp) − P(y+1bp)) / 2.
  - Expressed as: CLP per MM CLP face (BTP/Letras), CLP per 1,000 UF face (BTU, converted at today's UF), and USD at the dólar observado.
- **Auction DV01:** `dv01_auction = DV01_per_unit × auction size` (CLP and USD).
- **Weekly supply:** sum of auction DV01 by week, split by nominal/real and by tenor bucket (0–3y, 3–7y, 7–12y, 12–20y, 20y+).
- **Auction concession proxy:** `clearing_rate − fitted_yield(maturity, T-1)` in bp, labelled "vs fitted curve T-1". This is a proxy, not a true when-issued tail.
- **Tests:**
  - DV01 of a zero-coupon bond matches the closed form.
  - A par bond's price is 100 when the yield equals the coupon.
  - Unit conversions are correct.
  - The weekly aggregation buckets correctly.
  - The concession sign convention is right.

---

## Phase 3 — UI (`app/tabs/bond_monitor.py` + `app/tabs/bm_*.py`)

Every table and chart shows **source + as-of**, and a stale badge if the last successful scrape is older than expected. Each sub-tab has a **Refresh** button. On Auction Results it calls `get_results(con)`, which scrapes if a result is missing.

### Sub-tab 1 — Upcoming Auctions
- **Next auctions table:** date · weekday · time (Santiago and NY) · instrument · nemo · reopening? · size (native units) · size in USD · **DV01 (CLP and USD)** · countdown.
- **Weekly DV01 supply chart:** stacked bars by week, nominal vs real, colored by tenor bucket, for the rest of the quarter.
- **Calendar change log:** recent new, resized and removed auctions, with the date each was detected.
- Sources: Hacienda calendar (scraper), `bond_master`, `curve_fits`, BDE (UF, USD/CLP).

### Sub-tab 2 — Auction Results
- **History table**, filterable by instrument, nemo and date range: date · bond · nemo · cupo · demand · allocated · **bid-to-cover** · rate · concession vs fitted curve (bp) · % allocated to banks · % to AFPs/insurers/brokers · source (live/consolidated) · revised?
- **Charts:**
  - Bid-to-cover over time per instrument type
  - Investor allocation mix over time (stacked %)
  - Clearing rate vs the fitted curve yield on T-1
- **Latest auction card:** headline numbers vs that bond's last 5 auctions.
- Sources: Hacienda results (scraper + consolidated backfill), `curve_fits`.

### Sub-tab 3 — Supply & Issuance
- **Year-to-date issuance vs annual authorization** (USD): a progress bar, plus a cumulative line against a straight-line pace.
- Issuance by currency (CLP vs UF) and by tenor bucket, YTD and by quarter.
- **Amortization and coupon calendar**, from `bond_master` + `bond_outstanding`: upcoming maturities and coupon payments by month (cash returning to investors).
- Exchange/buyback operations list (if Phase 4 is done).
- Sources: Hacienda SOMA page, Estadísticas files, BCCh Agencia Fiscal.

### Sub-tab 4 — Bond Master & DV01
- **Bond table:** nemo · ISIN · type · currency · coupon · maturity · years to maturity · outstanding · yield (fitted, with the as-of date) · price · mod. duration · DV01 per unit (CLP, USD). Sortable, with a "flagged" column for decoded-from-nemo or missing master data.
- **DV01 calculator:** pick a bond, enter a face amount (MM CLP or thousands of UF), and get DV01 in CLP and USD, plus P&L for ±1/5/10bp.
- Sources: `bond_master`, `bond_outstanding`, `curve_fits`, BDE (UF, USD/CLP).

### Sub-tab 5 — Auction Impact (event study)
- For each auction: the change in the fitted yield at the auctioned maturity from **T−5 to T−1** (pre-auction concession build-up) and from **T−1 to T+5** (post-auction), in bp.
- Average path chart (T−5 … T+5) for nominal vs real, and split by bid-to-cover tercile.
- Scatter: concession vs bid-to-cover; concession vs auction DV01.
- Caption: "Based on fitted benchmark curves (BCCh BDE, T-1, transaction-based). Indicative, not bond-level."
- Sources: `auction_results`, `curve_fits`.

---

## Phase 4 — Jobs, scheduling, tests, docs
- **Jobs** (each also runnable from the CLI without the app):
  - `monitor.jobs.auctions_daily`: calendar + latest result + SOMA authorization text, weekdays 07:30 NY.
  - `monitor.jobs.auctions_watch`: wraps `hacienda.watch`, weekdays 08:00 NY. Exits if there's no auction today.
  - `monitor.jobs.auctions_evening`: results catch-up, weekdays 18:00 NY.
  - `monitor.jobs.bond_master_weekly`: master + outstanding + optional fiscal-agency docs, Mondays.
- Add the cron/launchd and GitHub Actions examples for these jobs to the README.
- Everything writes to `ingest_log`. The Data Health tab shows last success and staleness per job, the "needs review" bond list, and parser failures.
- **Tests:**
  - Real-page fixtures for calendar, results, the SOMA text and the Estadísticas links
  - The synthetic tests from `test_hacienda.py`
  - Backfill loader on the saved file
  - Nemo decoder vs master
  - The Phase 2 model tests
  - `get_results` refresh-on-consult
  - Smoke render of each sub-tab with fixture data
- Update CLAUDE.md: mark Bond Monitor as implemented; document the sources and formats (resolving the VERIFY items), the accumulation and refresh rules, auction timing, the pricing convention and its source, the nemo rule, and the concession proxy definition.
- **Final report:** what's done, the VERIFY outcomes (file formats, pricing convention, coupon frequency), anything blocked, and the suggested next spec (Global Macro).

---

## References
- Hacienda ODP auction calendar: https://www.hacienda.cl/areas-de-trabajo/finanzas-internacionales/oficina-de-la-deuda-publica/plan-emision/calendario-de-licitaciones
- Hacienda SOMA placements: https://www.hacienda.cl/areas-de-trabajo/finanzas-internacionales/oficina-de-la-deuda-publica/colocaciones-bcch-soma
- Hacienda latest result: https://www.hacienda.cl/areas-de-trabajo/finanzas-internacionales/oficina-de-la-deuda-publica/colocaciones-bcch-soma/resultados-ultima-licitacion
- Hacienda ODP statistics (consolidated results, bond characteristics, stock): https://www.hacienda.cl/areas-de-trabajo/finanzas-internacionales/oficina-de-la-deuda-publica/estadisticas
- BCCh Agencia Fiscal (auction rules, exchange operations): https://www.bcentral.cl/es/areas/mercados-financieros/operaciones-de-mercado-abierto-agencia-fiscal
- BCCh Informativo Diario (example): https://si3.bcentral.cl/estadisticas/Diario1/aplicaciones/Informativo/inf20260724.pdf
