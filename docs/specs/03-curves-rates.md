# Spec 03 — Curves & Rates tab

Status: **implemented** (2026-11-15). Source: Prompt 4 (user). Reuses the BDE ingestion, the implied TPM path (spec 01) and the UF model (spec 02).

## Decisions taken during implementation (user, 2026-11-15)
1. **NSS on the swap curves only** (nominal 8 points, real 7). The BDE has only 3 live nominal bond tenors (2/5/10Y) and real bonds often have 3, so bond curves show observed points only. Bond rolldown interpolates linearly between benchmarks (labelled "linear, not NSS"); bond forwards only between observed tenors (2y3y, 5y5y). The short end of the bond curve is never filled with swap rates.
2. **Nominal 1Y swap = traded SPC 360d** (`F022.SPC.TPR.D360.NO.Z.D`), converted ×365/360, because the 365-day close table has no nominal 1Y.
3. **FRED skipped**: every FRED dependency is marked "Pending" (UST yields for Chile vs World).
4. **No nominal 20Y** (bond or swap): nominal 10s20s / 5s10s20s show "n/a". Points 3–20 business days old get a hollow stale marker; older than 20 business days they're hidden from the curve (e.g. the 1Y bond benchmarks, last traded Dec 2025).

## Verified BDE codes (SearchSeries DAILY, 2026-11-15)
| Curve | Tenors live (obs in last year) | Codes | Not in the BDE |
|---|---|---|---|
| Nominal bonds | 2Y (181), 5Y (246), 10Y (184); 1Y stopped 2025-12-24 | `F022.BCLP.TIS.AN{01,02,05,10}.NO.Z.D` | 7Y, 20Y, 30Y |
| Nominal swaps | 3M/6M/1Y traded (250, 360 basis), 2/3/4/5/10Y (250, 365 basis) | `F022.SPC.TPR.D{090,180,360}.NO.Z.D`, `F022.SPC.TIN.AN{02,03,04,05,10}.NO.Z.D` | 1Y close, 20Y |
| Real bonds | 2Y (177), 5Y (247), 10Y (232), 20Y (56), 30Y BCU (67); 1Y stopped 2025-12-26 | `F022.BUF.TIS.AN{01,02,05,10,20,30}.UF.Z.D` | — |
| Real swaps | 1/2/3/4/5/10/20Y (250) | `F022.SPC.TIN.AN{01,02,03,04,05,10,20}.UF.Z.D` | 3M, 6M |

The Informativo Diario (checked 13-Nov-2026) lists the same benchmark tenors (pesos 1/2/5/10Y, UF 1/2/5/10/20Y), so it adds no points. BDE `F022.BTP/BTU.TIN.*` are auction results (irregular), not secondary-market rates: not used. Derived spreads match the BCCh-published breakevens (`F022.SWSP/BOSP.*`) within ~1bp at 2/5/10Y; the 1Y swap breakeven differs by ~7bp because its nominal leg is the traded 360d swap.

## Phase 0 findings: per-bond (nemotécnico) prices
No free, public, daily, machine-readable per-bond source was found. `bond_quotes` exists, empty.

| Source | Exists? | Access / format | Frequency, lag, history | Terms |
|---|---|---|---|---|
| Bolsa de Comercio de Santiago (www.bolsadesantiago.com) | Fixed-income trading exists; no public per-bond download found | Site is behind a Radware bot manager (automated requests get a JS challenge page), so it's unusable for automated fetching. Market data is sold under licence, distributed via Deutsche Börse and LSEG DataScope | Not public | Licensed (paid); not usable under the public-data rule |
| Bolsa Electrónica de Chile (www.bolchile.cl) | No public fixed-income price report found | Returns HTTP 503 to automated requests | — | — |
| BCCh Fiscal Agency (bcentral.cl, "Operaciones de Agencia Fiscal") | Yes: per-operation bases and financial conditions (e.g. BTP–BTU exchanges, window purchases), by nemotécnico | bcentral.cl HTML is bot-protected; manual save → `/documents/` PDFs | Irregular (per operation), not daily | Public documents |
| BCCh BDE | No per-instrument tables (only benchmark tenors, auction results by tenor, outstanding stocks) | REST | — | Public |
| BCCh Informativo Diario | Benchmark tenors only; auction results by tenor; nemotécnico rows only for BCCh forward purchases (usually empty) | PDF, si3.bcentral.cl | Daily, T-1 | Public |
| Pension-fund valuation prices (Superintendencia de Pensiones, RiskAmerica/DCV) | Daily prices exist and are sent to the AFPs | Not published openly; RiskAmerica is a commercial vendor | Daily | Paid |

**Best option for the Bond Monitor:** Hacienda/BCCh auction results by nemotécnico (irregular) plus the BDE benchmarks. A true per-bond daily curve would need a licensed feed.

---

Implement the **Curves & Rates** tab described in CLAUDE.md. Read CLAUDE.md first and follow its coding practices, storage schema and scraping rules. Reuse existing modules (`bcch.py`, `tpm_implied.py`, `uf.py`, `transforms.py`); don't duplicate them.

**How to work:**
- First, show me a plan: files, verified series codes, open questions.
- Build in phases. After each phase, run the tests and the app, commit, and report.
- Never guess BDE codes or URLs. Use `siete.buscar()` and save fixtures. If something isn't available, stop and tell me.

The tab gets **6 sub-tabs**: Curve Snapshot · Spreads & Shape · Breakevens & Real Rates · Carry & Rolldown · Chile vs World · Data Quality.

---

## Phase 0 — Source check: per-bond prices (investigate only, don't build)
Find out whether any **free, public, daily** source of per-bond prices or yields exists for BCP/BTP/BCU/BTU issues (individual nemotécnicos, not tenor benchmarks). Check:
- Bolsa de Comercio de Santiago (fixed-income statistics, daily reports/downloads)
- Bolsa Electrónica de Chile
- BCCh Fiscal Agency page (per-bond conditions for Treasury exchange operations; irregular)
- BCCh BDE (any per-instrument tables)

Report back: for each source, whether it exists, its URL, format, frequency, lag, history depth, and terms of use. **Don't build a scraper in this prompt.** Just create an empty `bond_quotes(nemo, obs_date, yield, price, volume, source, fetched_at)` table, so a feed can be added later for the Bond Monitor.

---

## Phase 1 — Data

### 1a. Raw curves (BDE, daily, transaction-based, may have gaps)
Register in `series.yaml` with `tab: curves_rates`. Store a `tenor_years` attribute for each series (3M = 0.25, 6M = 0.5, 1Y = 1, …).

| # | Curve | Instruments | Tenors (whatever exists in BDE) |
|---|---|---|---|
| 1 | **Nominal bonds** | BCP/BTP secondary-market rates | 1Y, 2Y, 5Y, 7Y, 10Y, 20Y, 30Y |
| 2 | **Nominal swaps** | Cámara (SPC) CLP | 3M, 6M, 1Y (traded, 360-day basis, T-1) + 1Y, 2Y, 3Y, 4Y, 5Y, 10Y (365-day basis, close) |
| 3 | **Real bonds** | BCU/BTU secondary-market rates | 1Y, 2Y, 5Y, 10Y, 20Y, 30Y |
| 4 | **Real swaps** | Cámara (SPC) UF | Same tenor set as #2, where available |

- Convert all swap rates to **one convention** before use (state which in CLAUDE.md). For the 1Y, which appears in both swap tables, use the 365-day close version and keep the other only for checks.
- Bond yields: confirm the BDE quoting convention (compounding, base) and document it.
- Also use FRED `DGS1, DGS2, DGS5, DGS10, DGS20, DGS30` for Chile vs World.

### 1b. Derived spread curves (computed, stored as series with `derived: true`)
| # | Series | Formula | Units |
|---|---|---|---|
| 5 | **Swap breakeven** | Cámara CLP − Cámara UF (same tenor, same obs_date) | bp |
| 6 | **Bond breakeven** | BTP − BTU (same tenor, same obs_date) | bp |
| 7 | **Swap spread** (labelled "Swap spread (Cámara − BTP)", **not** a true ASW) | Cámara CLP − BTP (same tenor, same obs_date) | bp |

- Compute only where **both legs are observed on the same obs_date and tenor**. Never fill in a missing leg. Tenors without a swap leg (20Y, 30Y) simply don't exist for #5 and #7.
- Also compute, as an option, a **model-based** version on a standard grid from the fitted curves (Phase 2a), flagged `model: true` and drawn differently in the UI.

### 1c. As-of logic (critical)
- All comparisons use the **data's own date** (`obs_date`), never the fetch date.
- `value_asof(series, target_date)` returns the last observation on or before `target_date`, **plus its actual date**. The UI shows the actual date used (e.g. "1W (07-Nov)").
- A point is **stale** if its latest observation is older than N business days (configurable, default 3). Stale points get a hollow marker and a tooltip.

---

## Phase 2 — Models (`src/monitor/models/curves.py`, pure functions, tested)

### 2a. Curve fitting
- **Nelson-Siegel-Svensson** fit per curve per day on the observed points (require ≥ 4 points, else skip with a reason). Bounded parameters and a robust initial guess (from the previous day's fit).
- Outputs: a fitted curve on a dense grid, fit residuals per tenor (bp), and an RMSE flag.
- Used for forwards, rolldown, model spreads and, later, Bond Monitor DV01. **The Curve Snapshot plots observed points, not the fit**, unless the "Show NSS fit" toggle is on.

### 2b. Other analytics
- Forwards from the fitted curves: 1y1y, 2y1y, 5y5y (nominal, real, breakeven).
- Spreads and flies: 2s10s, 5s10s, 10s20s, 2s5s10s, 5s10s20s for each curve.
- Rolling statistics: z-score and percentile rank over configurable windows.
- **PCA** on aligned daily changes (level, slope, curvature): loadings, explained variance, and factor scores over time.
- **Carry and rolldown** per tenor over 1M and 3M horizons:
  - Nominal: funding = the average expected overnight rate over the horizon, from `tpm_implied` (fall back to the current TPM if unavailable, and flag it).
  - Rolldown from the fitted curve.
  - Real/UF instruments: add the UF accrual from `uf.py` under the selected CPI scenario.
- **Tests:**
  - NSS fit recovers known parameters.
  - `value_asof` handles gaps and holidays.
  - Spreads are only computed where both legs exist.
  - A flat curve has zero rolldown.

---

## Phase 3 — UI

Every chart shows **source + as-of**. Each sub-tab has a **Refresh** button. Use Plotly.

### Sub-tab 1 — Curve Snapshot (detailed spec)

**Controls (top row)**
- **Series selector**, one of the 7 series: Nominal bonds · Nominal swaps · Real bonds · Real swaps · Swap breakeven · Bond breakeven · Swap spread (Cámara − BTP). Also a **"Grid" toggle** that shows all 7 as small multiples (2–3 per row) with the same controls applied.
- **Mode toggle: Values | Deltas**
- **Horizons (multiselect, default all):** 1D, 1W, 1M, 3M, 1Y
- **Boxplot window (dropdown, Values mode only):** 1W, 2W, 1M, 2M, 6M, 1Y, 2Y. Default 6M.
- **X-axis scale toggle:** Linear | √ (square-root). Default √.
- **Show NSS fit** (off by default) and **Show model spreads** (off by default; only for series 5–7)

**X axis**
- Numeric, in years (`tenor_years`). Spacing must reflect true maturity: 2Y sits much closer to 1Y than to 5Y.
- Tick labels show the tenor names (3M, 6M, 1Y, 2Y, 3Y, 5Y, 10Y, 20Y, 30Y) at their true positions, under both linear and √ scales.

**Values mode**
- **Today's curve:** solid line, **orange** (e.g. `#F28C28`), fully opaque, width 3, **solid markers at observed tenors only**.
- **Historical curves**, as of each selected horizon (via `value_asof`):

  | Horizon | Line | Opacity |
  |---|---|---|
  | 1D | dash | 0.85 |
  | 1W | dot | 0.70 |
  | 1M | dashdot | 0.55 |
  | 3M | longdash | 0.40 |
  | 1Y | longdashdot | 0.25 |

  The same orange hue for all, with solid markers at observed tenors. The legend shows the actual date used.
- **Background boxplots:** one box per tenor, computed from that series' **values** over the selected window.
  - Blue (e.g. `rgba(31,119,180,0.18)` fill, light outline), drawn **behind** the curves.
  - Box = p25–p75, median line, whiskers = min–max (state this in a caption).
  - Box width scales with local tenor spacing on the active x-scale, so boxes don't overlap at the short end.
  - Hover: tenor, window, n observations, min/p25/median/p75/max, **today's value and percentile**.
- Gaps: if a tenor has no observation for a curve, break the line there. **Never interpolate a missing observed point.**
- Y-axis units: % for outright curves (1–4), bp for spreads (5–7).

**Deltas mode**
- One line per selected horizon: Δ = today − value as of the horizon (bp), per tenor, computed only where both are observed.
- **1D:** solid line, **orange**, fully opaque, width 3, solid markers.
- 1W / 1M / 3M / 1Y: same orange, with the progressively more dotted dash styles and decreasing opacity from the Values table.
- Zero line emphasized. Hover shows both values, both dates, and Δ in bp.
- **Boxplots are hidden in Deltas mode** (they are value-based by definition).

**Below the chart**
- **Percentile table:** rows = tenors, columns = the 7 series, cell = today's percentile within the selected boxplot window. Color scale: ≤10th and ≥90th percentile highlighted. Clicking a cell selects that series.
- **Delta table:** rows = tenors, columns = 1D/1W/1M/3M/1Y Δ in bp, for the selected series.
- Caption: "Bond and swap data: BCCh BDE, end-of-day, transaction-based (T-1; tenors without trades are blank). Spread series are computed only where both legs traded on the same date."

### Sub-tab 2 — Spreads & Shape
- For each outright curve: 2s10s, 5s10s, 10s20s and the 2s5s10s and 5s10s20s flies. History chart with a selectable window, and current value, z-score and percentile (1Y and 3Y).
- History of the swap spread and of both breakevens at the 2Y, 5Y and 10Y tenors.
- **PCA panel:** loadings chart, explained variance, and factor scores over time (nominal swaps and nominal bonds).
- Sources: BDE; derived series.

### Sub-tab 3 — Breakevens & Real Rates
- Breakeven curves (swap and bond) today vs 1M and 1Y ago.
- Forward breakevens: 1y1y, 2y3y, 5y5y.
- Breakeven vs EEE inflation expectations (11m, 23m, 35m) and the 3% target. **Breakeven − survey** as an inflation risk premium proxy.
- Real yield history at the 5Y, 10Y and 20Y tenors.
- Sources: BDE (bonds, swaps, compensación inflacionaria series for cross-checking), EEE.

### Sub-tab 4 — Carry & Rolldown
- Table per tenor and curve (nominal bonds, nominal swaps, real bonds, real swaps):

  | Column | Notes |
  |---|---|
  | Yield | |
  | 1M and 3M carry | |
  | 1M and 3M rolldown | |
  | Total | |
  | UF accrual | Real instruments only, with the CPI scenario selectable as in Chile Macro |
  | Realized vol | Std of daily Δ over 3M |
  | Carry+roll / vol | |

- Scatter: (carry + roll) vs vol, labelled by instrument and tenor.
- The funding assumption is shown explicitly ("Funding: SPC-implied TPM path as of DATE").
- Sources: BDE; `tpm_implied`; `uf.py`.

### Sub-tab 5 — Chile vs World
- Chile nominal 2Y/5Y/10Y (bonds and swaps) vs UST 2Y/5Y/10Y: levels and spreads, with z-scores.
- Chile and US forwards: 1y1y, 5y5y.
- Placeholder for Mexico, Colombia and Brazil, to be filled when Global Macro is built (Banxico SIE, BanRep, BCB SGS).
- Sources: BDE, FRED.

### Sub-tab 6 — Data Quality
- Heatmap: tenor × date (last 3M), showing which points were observed vs missing, per curve.
- Per tenor: % of days observed (1Y), last observed date, staleness.
- NSS fit diagnostics: RMSE over time, days skipped and why.

---

## Phase 4 — Jobs, tests, docs
- Extend the daily BDE job for all curve series; compute derived spreads and NSS fits after ingestion. Store fits in a `curve_fits(curve, obs_date, params_json, rmse, n_points)` table.
- Log everything to `ingest_log`, and add the series to the Data Health tab.
- **Tests:**
  - Parsers (fixtures)
  - The Phase 2 model tests
  - Tenor-axis mapping: tick positions correct under linear and √
  - The Curve Snapshot builder returns the expected traces for each mode (Values with boxplots; Deltas without), using fixture data
- Update CLAUDE.md: verified codes, rate conventions, the as-of rule, the spread definitions (including "swap spread ≠ ASW"), NSS settings, the funding assumption, and the Phase 0 findings on per-bond sources.
- Final report: done / VERIFY / blocked, the Phase 0 per-bond findings, and the suggested next spec (Bond Monitor).

---

## References
- BCCh BDE API docs: https://si3.bcentral.cl/estadisticas/Principal1/Web_Services/documentacion.html
- SPC by tenor (365 basis, close): https://si3.bcentral.cl/siete/ES/Siete/Cuadro/CAP_TASA_INTERES/MN_TASA_INTERES_09/TI__TPM4
- SPC 90/180/360d (360 basis, T-1): https://si3.bcentral.cl/siete/ES/Siete/Cuadro/CAP_TASA_INTERES/MN_TASA_INTERES_09/TI__TPM4_DIAS
- BCCh Informativo Diario (bond and SPC rates, example): https://si3.bcentral.cl/estadisticas/Diario1/aplicaciones/Informativo/inf20260724.pdf
- BCCh Fiscal Agency operations (per-bond exchange conditions): https://www.bcentral.cl/es/areas/mercados-financieros/operaciones-de-mercado-abierto-agencia-fiscal
- FRED Treasury yields: https://fred.stlouisfed.org/categories/115
- Nelson-Siegel-Svensson: Svensson (1994), "Estimating and Interpreting Forward Interest Rates", NBER WP 4871
