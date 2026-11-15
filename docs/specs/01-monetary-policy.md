# Spec 01 — Monetary Policy tab

Status: **planning** (saved 2026-11-14).

Implement the **Monetary Policy** tab described in CLAUDE.md. Read CLAUDE.md first and follow its coding practices, storage schema and scraping rules.

**How to work:**
- First, show a plan: files to create or modify, the data sources and series codes, and any open questions.
- Build in the **phases** below. After each phase, run the tests and the app, commit, and give a short report before moving on.
- Never guess BDE series codes, URLs or API fields. Discover them: use `monitor.sources.bcch.search_series()` (BDE SearchSeries; replaces `siete.buscar()`), read real HTML/JSON responses, and save samples to `tests/fixtures/`. If something can't be found, stop and report.
- The tab gets **5 sub-tabs**: Overview · Board & Communication · Decision History · Implied Path & Probabilities · Surveys.

---

## Phase 1 — Data foundations

### 1a. Meeting calendar (`config/meetings.yaml` + `src/monitor/sources/bcch_web.py`)
- Scrape the BCCh monetary policy meeting (RPM) calendar from bcentral.cl for the current year and next year. Fill `meetings.yaml` with: `meeting_id`, `start_date`, `decision_date`, `decision_time_local` (America/Santiago), `effective_date` (the business day after the decision), `has_ipom` (true for the meetings that coincide with the Monetary Policy Report).
- Also backfill past meetings as far back as the site allows (needed for Decision History). If the past calendar isn't published in a structured way, derive historical decision dates from the statement archive in 1c.
- Source: bcentral.cl (scraping). Fallback: manual edits to the YAML.

### 1b. BDE series (`src/monitor/sources/bcch.py`)
Implement fetches for the following, and register each in `series.yaml` with its verified code:
- **TPM** (daily)
- **Cámara swap curve, nominal:** traded 90d, 180d and 360d (360-day basis, T-1), plus the tenor table at 1y, 2y, 3y, 4y, 5y and 10y (365-day basis, close). Also the **UF** versions where they exist.
- **Interbank overnight rate** (TIB), for checking the TIB–TPM spread.
- **EEE** medians: TPM at the next meeting, the subsequent meeting, in 5 months, in 11 months and in 23 months; plus the December-of-year values if they're published as series.
- **EOF** medians (pre- and post-meeting): TPM expectations for the meetings it covers.
- **CPI** headline and core (base 2023) and IMACEC, for the reaction-function context in Decision History.
- Store everything in `observations` with vintages, per CLAUDE.md.

### 1c. Documents (`src/monitor/sources/bcch_web.py`)
These documents are downloaded and stored locally. Several features depend on the full text: the statement diff, vote record, dissent rationale and hawk/dove scores. Every document gets a `documents` row (kind, meeting_id, published_at, URL, sha256, extracted text), and the original file is kept under `data/raw/bcch_web/<kind>/`.

| Document | Format (VERIFY) | Needed for | Timing | Priority |
|---|---|---|---|---|
| **Policy statements** (comunicados de RPM) | HTML and/or PDF | Diff viewer, vote/dissent extraction, hawk/dove score, decision dates | Evening of the decision | Required |
| **Minutes** (actas / minutas de RPM) | PDF | Vote detail and dissent rationale, policy options discussed, hawk/dove score, key passages | About 2 weeks after the meeting | Required |
| **IPoM** (Monetary Policy Report, quarterly) | PDF + any accompanying Excel/CSV data files | TPM corridor (p10/p50/p90), BCCh forecast table (CPI, GDP, assumptions), hawk/dove score of the summary/opening chapter | 4×/year, aligned with specific meetings | Required (corridor + forecasts); full-text scoring optional |
| **Board speeches / presentations** (e.g. IPoM presentation to the Senate) | PDF/HTML | Extra communication signal for hawk/dove tracking | Irregular | Optional, later phase |

- Download the **full archive** for statements and minutes, as far back as the site provides. For the IPoM, at least 2019 onward.
- PDFs: extract text with pdfplumber, strip headers, footers and page numbers, and keep paragraph breaks. If a PDF is scanned (no text layer), log it and skip. No OCR without asking.
- **Incremental:** on each run, fetch only documents whose URL or sha256 is new. Re-download only if the sha256 changes, and log the change.
- **Minutes lag:** until the minutes are published, the Decision History row shows "minutes pending (expected ~DATE)".
- **Board members** (Consejo): scrape name, role (President / Vice President / Board member), term start and term end, and photo URL if available. Store as `config/board.yaml` (cache) or a `board_members` table. Keep historical members too, if listed, for the vote record.
- **IPoM TPM corridor and forecasts:** find where the BCCh publishes the TPM corridor and the forecast table (in the IPoM PDF or its accompanying data files). If machine-readable data exists, ingest it: `series_id=ipom_tpm_corridor_{p10,p50,p90}` and `ipom_forecast_{cpi,core_cpi,gdp}_{year}`, with vintage = IPoM date. If the corridor only exists as a chart image, report it and add a manual-entry YAML (`config/ipom_corridor.yaml`).

### 1d. Prediction markets (`src/monitor/sources/kalshi.py`, `polymarket.py`)
- **Kalshi:** public Trade API v2 (no key). Base URL `https://external-api.kalshi.com/trade-api/v2`, fallback `https://api.elections.kalshi.com/trade-api/v2`. Use series `KXCBDCHILE`. Get events with nested markets, and order books or best bid/ask for each outcome market. Inspect the real JSON for field names; prices may be in cents or dollars.
  - Implied probability per outcome = bid/ask midpoint, then normalize so the outcomes sum to 100%. Store the raw sum before normalization as an "overround" quality metric.
  - Save each pull to `market_snapshots`.
  - Backfill history using the candlesticks endpoint for each market.
  - Add the job `monitor.jobs.snapshot_markets`, meant to run hourly (and also run on Refresh).
- **Polymarket:** search the public API for a BCCh / Chile rate market. If one exists, treat it the same way. If not, log it and skip gracefully.

---

## Phase 2 — Models (`src/monitor/models/`)

All pure functions with unit tests.

### 2a. `tpm_implied.py` — market-implied TPM path
- **Model:** the TPM is a step function that only changes on meeting effective dates. The overnight Cámara rate = TPM + spread, where the spread is the recent average of TIB − TPM (default 0, configurable).
- **Pricing (≤ 1y swaps, single payment at maturity):** the fixed rate R on an ACT/360 simple basis satisfies `1 + R·T/360 = Π_d (1 + r_d · n_d/360)` over business days d, where r_d is the overnight rate and n_d the calendar days to the next business day. Use a Chilean holiday calendar (the `holidays` package, country "CL"; ask before adding it).
- **Longer tenors (2y):** confirm the SPC coupon convention for longer tenors from a BCCh or market source and document it. If it can't be confirmed, use the 2y point only as a weak, low-weight constraint and flag it in the UI.
- **Day-count harmonization:** traded 90/180/360d are on a 360-day basis; the tenor table is on a 365-day basis. Convert before fitting.
- **Fit:** solve for the post-meeting TPM levels `x_1..x_N` for the next N meetings (default N = 8, about 1 year), minimizing
  `Σ_s w_s (model_s − market_s)²  +  λ Σ_i (x_i − prior_i)²  +  μ Σ_i (x_i − x_{i-1})²`
  - `prior_i` comes from the EEE/EOF medians, interpolated to meeting dates.
  - The first meeting can optionally be anchored to the Kalshi expected rate, with a high weight.
  - λ and μ are configurable. Show a fit-residuals table in the UI (model vs market per tenor, in bp).
- **Outputs:** expected TPM after each meeting; expected change per meeting and cumulative; FedWatch-style probabilities (split the expected change between the two nearest 25bp outcomes); a "fit quality" flag.
- **Tests:** a flat path reprices a flat curve; a known 25bp hike at meeting 1 reproduces the right 90d rate; day-count conversion round-trips; with λ = μ = 0 and as many swaps as unknowns, the fit is exact.

### 2b. `surprises.py`
- For each historical decision: `surprise_bp = actual_change − expected_change`.
  - Primary expectation: the latest EEE/EOF median published before the meeting.
  - Secondary: the market-implied change from SPC 90d on the day before, where data exists.
- Summary stats: hit rate, average absolute surprise, surprises by year and by board president.

### 2c. `text_analysis.py`
- **Statement diff:** word-level diff of each statement vs the previous one (difflib), rendered as HTML with additions in green and deletions in red.
- **Vote extraction:** parse unanimity or dissents, and who dissented and for what, using regex on the Spanish phrasing. Unit-test against real fixtures. LLM fallback only if regex fails.
- **Hawk/dove score (optional; only if `ANTHROPIC_API_KEY` is set):** score each statement and minutes from −2 (very dovish) to +2 (very hawkish), with a one-line rationale and key phrases. Store in a `doc_scores` table keyed by doc sha256, so nothing is ever re-scored. Without the key, hide the feature and show a note.

---

## Phase 3 — UI (`app/tabs/monetary_policy.py` + `app/tabs/mp_*.py`)

Every chart and table shows **source + as-of timestamp**, and a stale badge if data is older than expected. Each sub-tab has a **Refresh** button that runs only the jobs it needs.

### Sub-tab 1 — Overview
- **KPI row:** current TPM · next meeting date with a countdown (Santiago and NY times) · Kalshi probabilities for the next meeting · SPC-implied expected change for the next meeting · EEE/EOF median · last decision and vote · latest hawk/dove score (if available).
- A compact implied-path chart, linking to the full sub-tab.
- "What changed since last meeting": TPM path shift, survey shift, statement diff summary.

### Sub-tab 2 — Board & Communication
- **Board panel:** a card per member (name, role, term end, years on board) and their dissent count.
- **Statement archive:** a selectable list; the selected statement shows full text plus a **diff vs the previous statement**.
- **Minutes archive:** same, with key passages; summary if the API key is available.
- **Hawk/dove chart:** score over time for statements and minutes, overlaid on the TPM (dual axis).
- **Vote record table:** meeting · decision · vote split · dissenters and their preferred action.

### Sub-tab 3 — Decision History & Reaction Function
- **Table:** every meeting — date, decision (bp), new TPM, vote, EEE/EOF expected change, surprise (bp), CPI y/y and core y/y at the time, IMACEC y/y.
- **Charts:** TPM vs CPI headline and core with the 2–4% target band (TPM as a step line, meetings marked); a surprise bar chart over time.
- **Stats panel:** hit rate, average absolute surprise, share of moves that were 25bp, 50bp or larger, average move size in hiking vs cutting cycles.

### Sub-tab 4 — Implied Path & Probabilities
- **Main chart ("Market vs Survey vs BCCh"):** SPC-implied TPM path with a band from fit residuals and λ/μ sensitivity; EEE median path; EOF median path; IPoM corridor (p10/p50/p90) if available; Kalshi expected rate for the next meeting; current TPM line.
- **Per-meeting table:** meeting · days to meeting · implied post-meeting TPM · expected change · probabilities (cut 50 / cut 25 / hold / hike 25 / hike 50) from SPC · Kalshi probabilities · survey expectation · market − survey (bp).
- **Kalshi panel:** outcome probabilities with bid/ask, the overround, and a probability history chart (snapshots + candlesticks). Polymarket alongside, if found.
- **Fit diagnostics (expander):** input swap rates and their dates (T-1 clearly shown), day-count bases, model vs market residuals in bp, λ/μ sliders, Kalshi-anchor toggle.
- **Labeling:** "SPC data is end-of-day, T-1 (BCCh BDE). Kalshi is live."

### Sub-tab 5 — Surveys
- **EEE:** TPM expectations at each horizon over time. Latest survey: median, p10, p90, respondent count if available.
- **EOF:** pre- vs post-meeting TPM expectations, and the shift after each meeting.
- **Survey vs market:** EEE "next meeting" vs SPC-implied expectation on the survey publication date (scatter + time series).
- Sources: BDE (EEE, EOF); survey PDFs only for fields not in the BDE.

---

## Phase 4 — Jobs, tests, docs
- `monitor.jobs.mp_daily`: BDE series, new statements and minutes, board page (weekly is enough), surveys.
- `monitor.jobs.snapshot_markets`: Kalshi/Polymarket, hourly.
- All jobs write to `ingest_log`; the Data Health tab shows them.
- **Tests:** parsers against saved fixtures (statement HTML, minutes, board page, Kalshi JSON, BDE responses); Phase 2 model tests; a smoke test rendering each sub-tab with fixture data.
- Update CLAUDE.md: Monetary Policy implemented, verified series codes, scraping targets, assumptions (SPC convention, spread, λ/μ defaults).
- Final report: done, VERIFY/blocked, description of each sub-tab, suggested next spec.

---

## References
- BCCh BDE API docs: https://si3.bcentral.cl/estadisticas/Principal1/Web_Services/documentacion.html
- SPC by tenor (365 basis, close): https://si3.bcentral.cl/siete/ES/Siete/Cuadro/CAP_TASA_INTERES/MN_TASA_INTERES_09/TI__TPM4
- SPC 90/180/360d traded (360 basis, T-1): https://si3.bcentral.cl/siete/ES/Siete/Cuadro/CAP_TASA_INTERES/MN_TASA_INTERES_09/TI__TPM4_DIAS
- BCCh surveys (EEE / EOF) calendar: https://www.bcentral.cl/contenido/-/detalle/encuestas-de-expectativas-2026
- EEE results example (TPM horizons): https://www.bcentral.cl/documents/33528/8691614/Gr%C3%A1ficos+%28distribuci%C3%B3n+de+respuestas%29+octubre+2026.pdf
- Kalshi public market data: https://docs.kalshi.com/getting_started/quick_start_market_data
- Kalshi BCCh December market: https://kalshi.com/markets/kxcbdchile/central-bank-of-chile-interest-rate-decision/kxcbdchile-26dec15
- BCCh studies on SPC: Estudio Económico Estadístico N°133 (SPC market) and N°56 (SPC & Seguro Inflación), bcentral.cl
