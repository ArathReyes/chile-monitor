# Backtest 01: market-implied TPM path

Generated 2026-11-15 by `scripts/backtest_implied_path.py` from a copy of `data/monitor.duckdb`. Model: `monitor.models.tpm_implied.fit_path`; harness: `monitor.models.backtest`. Per-row results: `01-implied-path.csv`.

## Method

- Sample: 45 of 45 confirmed BCCh meetings (2021-05-13 to 2026-10-27, holds included). Steps use true meeting dates (the BCCh publishes its calendar in advance).

- For each meeting and horizon h ∈ {1, 5, 20} CL business days, the as-of date is the decision date minus h business days. Every input is restricted to that date and the path is refit (N = 8 steps, Kalshi anchor off, 2y swap off).

- Realized change at meeting 1 = TPM on the effective date − TPM on the decision date. Realized cumulative change to meetings 2 and 3 = TPM on their effective dates − TPM at the as-of date (same base as the implied cumulative change).

- Benchmarks: **EOF survey** (`survey_expectation` logic, cutoff = as-of date; position-aware so a round taken before an intervening meeting uses the right key; EEE fallback for meeting 1 only), **random walk** (no change), **SPC-90d single step** (`market_implied_change` logic with the as-of quote: one move, no spread, no prior).

## Information-set assumptions (no look-ahead)

- SPC 90/180/360d: latest quote dated ≤ as-of (BDE date = trade date, quote = close of that day), tenor skipped if older than 7 days; accrual starts T+2 CL business days (`spc_start`), 360 basis.

- EOF: a round dated d is used from d + 1 day (results are published after the survey closes). The benchmark needs a round within 45 days of the decision. The prior uses all keys from the single latest round (the app takes each series' latest value separately, which can mix rounds).

- EEE: dated the 1st of the month, used 12 days later.

- TPM, TIB (spread = 20-obs average TIB − TPM): observations dated ≤ as-of.

- Prior mapping as in `app/mp_data.implied_fit` (EOF next, rpm1, rpm2_3m, rpm3, rpm4_6m by meeting position; 12m → 365 days, 24m → 730 days), except meeting positions are shifted by the number of meetings held between the EOF round and the as-of date (see 'Prior alignment' below).

- Direction hit: sign of change with a ±5bp dead band (inside = hold). 25bp bucket hit: implied change rounded to the nearest 25bp equals the realized change.

## Headline: meeting-1 change (all regimes)

| h (bd) | method | n | mae_bp | rmse_bp | bias_bp | dir hit % | 25bp bucket % |
|---|---|---|---|---|---|---|---|
| 1 | EOF survey | 45 | 7.8 | 16.7 | -3.3 | 100.0 | 75.6 |
| 1 | Implied path | 45 | 10.0 | 13.8 | -4.5 | 77.8 | 71.1 |
| 1 | Random walk (0) | 45 | 38.9 | 61.0 | -8.9 | 46.7 | 46.7 |
| 1 | SPC-90d single step | 45 | 17.8 | 26.6 | -3.1 | 71.1 | 53.3 |
| 5 | EOF survey | 45 | 10.0 | 19.0 | -6.7 | 95.6 | 68.9 |
| 5 | Implied path | 45 | 11.2 | 15.3 | -6.4 | 75.6 | 73.3 |
| 5 | Random walk (0) | 45 | 38.9 | 61.0 | -8.9 | 46.7 | 46.7 |
| 5 | SPC-90d single step | 45 | 15.0 | 23.4 | -4.7 | 71.1 | 51.1 |
| 20 | EOF survey | 41 | 19.5 | 30.7 | -11.0 | 85.4 | 48.8 |
| 20 | Implied path | 43 | 18.1 | 25.1 | -12.1 | 69.8 | 44.2 |
| 20 | Random walk (0) | 45 | 38.9 | 61.0 | -8.9 | 46.7 | 46.7 |
| 20 | SPC-90d single step | 45 | 18.8 | 27.9 | -8.9 | 68.9 | 46.7 |

## Cumulative change to meetings 2 and 3

SPC-90d single step has no multi-meeting version; random walk = 0.

### To meeting 2

| h (bd) | method | n | mae_bp | rmse_bp | bias_bp | dir hit % | 25bp bucket % |
|---|---|---|---|---|---|---|---|
| 1 | EOF survey | 44 | 23.3 | 40.4 | -15.3 | 93.2 | 54.5 |
| 1 | Implied path | 44 | 26.4 | 39.7 | -22.4 | 79.5 | 43.2 |
| 1 | Random walk (0) | 44 | 79.5 | 120.3 | -18.2 | 34.1 | 34.1 |
| 5 | EOF survey | 43 | 33.1 | 53.4 | -23.2 | 90.7 | 41.9 |
| 5 | Implied path | 44 | 30.1 | 45.9 | -26.1 | 77.3 | 45.5 |
| 5 | Random walk (0) | 44 | 79.5 | 120.3 | -18.2 | 34.1 | 34.1 |
| 20 | EOF survey | 26 | 43.3 | 56.9 | -28.8 | 84.6 | 23.1 |
| 20 | Implied path | 42 | 42.8 | 63.3 | -36.5 | 66.7 | 42.9 |
| 20 | Random walk (0) | 44 | 80.7 | 120.7 | -17.0 | 34.1 | 34.1 |

### To meeting 3

| h (bd) | method | n | mae_bp | rmse_bp | bias_bp | dir hit % | 25bp bucket % |
|---|---|---|---|---|---|---|---|
| 1 | EOF survey | 43 | 58.4 | 85.3 | -42.7 | 83.7 | 23.3 |
| 1 | Implied path | 43 | 57.5 | 84.2 | -51.9 | 79.1 | 27.9 |
| 1 | Random walk (0) | 43 | 122.1 | 178.2 | -27.9 | 23.3 | 23.3 |
| 5 | EOF survey | 42 | 62.8 | 95.7 | -47.9 | 85.7 | 26.2 |
| 5 | Implied path | 43 | 61.5 | 90.6 | -56.2 | 79.1 | 27.9 |
| 5 | Random walk (0) | 43 | 122.1 | 178.2 | -27.9 | 23.3 | 23.3 |
| 20 | EOF survey | 26 | 68.3 | 96.0 | -51.0 | 80.8 | 15.4 |
| 20 | Implied path | 41 | 72.5 | 106.7 | -65.3 | 75.6 | 22.0 |
| 20 | Random walk (0) | 43 | 123.3 | 178.7 | -26.7 | 23.3 | 23.3 |

## Meeting-1 MAE (bp) by regime

Hiking 2021-05..2022-10, plateau 2022-11..2023-06, cutting 2023-07..2025-12, 2026.

| regime | h (bd) | n | EOF survey | Implied path | Random walk (0) | SPC-90d single step |
|---|---|---|---|---|---|---|
| hiking | 1 | 13 | 21.2 | 17.8 | 82.7 | 25.1 |
| hiking | 5 | 13 | 23.1 | 20.2 | 82.7 | 19.8 |
| hiking | 20 | 12 | 37.5 | 31.2 | 82.7 | 29.3 |
| plateau | 1 | 5 | 0.0 | 14.1 | 0.0 | 22.7 |
| plateau | 5 | 5 | 0.0 | 15.2 | 0.0 | 18.5 |
| plateau | 20 | 5 | 0.0 | 23.8 | 0.0 | 12.9 |
| cutting | 1 | 20 | 3.8 | 6.5 | 33.8 | 16.6 |
| cutting | 5 | 20 | 6.2 | 7.2 | 33.8 | 14.8 |
| cutting | 20 | 19 | 17.1 | 12.7 | 33.8 | 17.3 |
| 2026 | 1 | 7 | 0.0 | 2.9 | 0.0 | 4.2 |
| 2026 | 5 | 7 | 3.6 | 2.8 | 0.0 | 3.7 |
| 2026 | 20 | 7 | 4.2 | 6.2 | 0.0 | 7.7 |

## λ/μ sensitivity (h = 1 bd)

λ = prior weight, μ = smoothness weight (per bp², swap weight 1). Current defaults λ = 0.005, μ = 0.25.

The spec grid is μ ∈ {0.05, 0.25, 1}; μ = 0 and 0.01 were added after the spec grid showed MAE falling as μ falls.

| lam | mu | n_m1 | mae_m1 | rmse_m1 | bucket_m1 | n_cum3 | mae_cum3 | rmse_cum3 |
|---|---|---|---|---|---|---|---|---|
| 0.00 | 0.00 | 45 | 10.53 | 15.37 | 0.71 | 43 | 61.33 | 85.24 |
| 0.00 | 0.01 | 45 | 9.26 | 12.80 | 0.76 | 43 | 57.66 | 82.32 |
| 0.00 | 0.05 | 45 | 8.85 | 12.16 | 0.80 | 43 | 57.35 | 82.53 |
| 0.00 | 0.25 | 45 | 9.97 | 13.76 | 0.71 | 43 | 57.61 | 84.41 |
| 0.00 | 1.00 | 45 | 15.70 | 22.18 | 0.58 | 43 | 59.27 | 90.35 |
| 0.01 | 0.00 | 45 | 9.44 | 14.62 | 0.78 | 43 | 61.17 | 85.66 |
| 0.01 | 0.01 | 45 | 9.11 | 12.90 | 0.78 | 43 | 58.04 | 82.76 |
| 0.01 | 0.05 | 45 | 8.82 | 12.18 | 0.80 | 43 | 57.27 | 82.33 |
| 0.01 | 0.25 | 45 | 10.02 | 13.81 | 0.71 | 43 | 57.48 | 84.20 |
| 0.01 | 1.00 | 45 | 15.64 | 22.09 | 0.58 | 43 | 59.24 | 90.29 |
| 0.02 | 0.00 | 45 | 9.42 | 14.52 | 0.78 | 43 | 60.31 | 84.76 |
| 0.02 | 0.01 | 45 | 8.94 | 13.12 | 0.80 | 43 | 58.38 | 82.92 |
| 0.02 | 0.05 | 45 | 8.79 | 12.29 | 0.78 | 43 | 57.13 | 81.92 |
| 0.02 | 0.25 | 45 | 10.16 | 13.93 | 0.71 | 43 | 57.22 | 83.69 |
| 0.02 | 1.00 | 45 | 15.47 | 21.84 | 0.58 | 43 | 59.13 | 90.11 |
| 0.10 | 0.00 | 45 | 9.76 | 14.67 | 0.71 | 43 | 58.50 | 82.44 |
| 0.10 | 0.01 | 45 | 9.51 | 14.06 | 0.71 | 43 | 57.87 | 81.87 |
| 0.10 | 0.05 | 45 | 9.36 | 13.26 | 0.71 | 43 | 56.74 | 81.08 |
| 0.10 | 0.25 | 45 | 10.60 | 14.29 | 0.69 | 43 | 56.48 | 82.34 |
| 0.10 | 1.00 | 45 | 14.72 | 20.78 | 0.58 | 43 | 58.82 | 89.12 |

### Candidate settings at every horizon

| lam | mu | h (bd) | mae_m1 | rmse_m1 | bias_m1 | bucket_m1 % | mae_cum3 | rmse_cum3 |
|---|---|---|---|---|---|---|---|---|
| 0.01 | 0.25 | 1 | 10.02 | 13.81 | -4.50 | 71.11 | 57.48 | 84.20 |
| 0.01 | 0.25 | 5 | 11.16 | 15.27 | -6.38 | 73.33 | 61.47 | 90.59 |
| 0.01 | 0.25 | 20 | 18.10 | 25.11 | -12.07 | 44.19 | 72.47 | 106.73 |
| 0.01 | 0.05 | 1 | 8.82 | 12.18 | 0.76 | 80.00 | 57.27 | 82.33 |
| 0.01 | 0.05 | 5 | 9.27 | 13.29 | -0.82 | 77.78 | 61.41 | 88.19 |
| 0.01 | 0.05 | 20 | 14.65 | 21.11 | -5.22 | 58.14 | 72.74 | 105.82 |
| 0.02 | 0.05 | 1 | 8.79 | 12.29 | 0.26 | 77.78 | 57.13 | 81.92 |
| 0.02 | 0.05 | 5 | 9.15 | 13.22 | -1.26 | 77.78 | 61.05 | 87.79 |
| 0.02 | 0.05 | 20 | 14.83 | 21.07 | -5.62 | 60.47 | 71.84 | 104.59 |
| 0.01 | 0.01 | 1 | 9.11 | 12.90 | 2.59 | 77.78 | 58.04 | 82.76 |
| 0.01 | 0.01 | 5 | 9.61 | 14.09 | 1.37 | 75.56 | 62.36 | 88.18 |
| 0.01 | 0.01 | 20 | 14.51 | 20.79 | -1.90 | 62.79 | 73.55 | 106.28 |

## Worst meeting-1 misses (h = 1 bd, default λ/μ)

| decision_date | as_of | actual_change_bp | model_m1_bp | err_bp | eof_m1_bp | spc90_m1_bp | prior_m1_bp | spread_bp | fit_rmse_bp |
|---|---|---|---|---|---|---|---|---|---|
| 2022-09-06 | 2022-09-05 | 100.0 | 60.9 | -39.1 | 75.0 | 96.9 | 75.0 | 0.0 | 14.4 |
| 2022-05-05 | 2022-05-04 | 125.0 | 87.8 | -37.2 | 100.0 | 145.2 | 100.0 | -0.9 | 13.2 |
| 2023-06-19 | 2023-06-16 | 0.0 | -27.8 | -27.8 | 0.0 | -51.5 | 0.0 | 0.0 | 24.6 |
| 2021-08-31 | 2021-08-30 | 75.0 | 47.8 | -27.2 | 25.0 | 68.7 | 25.0 | 0.0 | 7.9 |
| 2022-01-26 | 2022-01-25 | 150.0 | 125.9 | -24.1 | 125.0 | 160.1 | 125.0 | 0.0 | 12.3 |
| 2022-07-13 | 2022-07-12 | 75.0 | 96.9 | 21.9 | 50.0 | 130.9 | 50.0 | 0.0 | 14.9 |
| 2023-01-26 | 2023-01-25 | 0.0 | -20.3 | -20.3 | 0.0 | -18.1 | 0.0 | 0.0 | 22.7 |
| 2023-09-05 | 2023-09-04 | -75.0 | -91.6 | -16.6 | -75.0 | -121.0 | -75.0 | -0.0 | 17.0 |

## Prior alignment

Rows where the latest EOF round predates a meeting already held by the as-of date (27 rows). The app's mapping assigns the round's 'next' to the first future step, i.e. one meeting too late.

| prior | target | n | mae_bp | rmse_bp |
|---|---|---|---|---|
| aligned prior (backtest) | m1 | 27 | 19.7 | 27.8 |
| aligned prior (backtest) | cum3 | 26 | 75.0 | 112.9 |
| app mapping (unaligned) | m1 | 27 | 19.9 | 27.9 |
| app mapping (unaligned) | cum3 | 26 | 75.0 | 112.9 |

## Skipped rows

| meeting_id | decision_date | horizon_bd | as_of | skip_reason |
|---|---|---|---|---|
| RPM-2021-06 | 2021-06-08 | 20 | 2021-05-10 | target is not steps[0] (2021-05-13) |
| RPM-2024-06 | 2024-06-18 | 20 | 2024-05-20 | target is not steps[0] (2024-05-23) |

## Conclusions

1. **Meeting 1, short horizons.** At h = 1-5 bd the implied path cuts the random-walk error by
   ~75% and beats the SPC-90d single-step approximation clearly (MAE ~10 vs ~15-18bp), which
   systematically overshoots because it loads every move inside the 90-day window onto one
   meeting. The EOF survey has the lowest MAE at h = 1 (it lands exactly on the 25bp grid when
   right) but a higher RMSE than the model: when the survey misses (hiking cycle) it misses big.
2. **h = 20 bd.** All methods degrade; the model and SPC-90d are close to the survey, and the
   bucket hit rate drops below 50%. Read the path 4 weeks out as direction, not size.
3. **Regimes.** Most error comes from the 2021-22 hiking cycle (outsized hikes beyond survey and
   market pricing). In the cutting cycle and 2026 the model is within ~3-7bp at h = 1. In the
   2022-23 plateau the curve priced cuts early (inverted SPC), so the model shows -10 to -30bp at
   holds; the survey (0) was right there.
4. **Smoothness is too stiff.** With μ = 0.25 the fit RMSE on the three swaps is 10-25bp in
   2021-23 (vs ~4bp with μ = 0.05): the penalty on a 100bp step (2,500) dwarfs a 14bp swap miss
   (~200 per swap), so large moves get spread over neighbouring meetings. That is the source of
   the negative bias in the hiking cycle and of the early-cut miss in Jun-2023.
5. **Cumulative to meetings 2-3** is hard for everyone (MAE ~57-62bp at h = 1, n = 43); the model
   is on par with the EOF and far better than a random walk. λ barely matters (3 swaps pin the
   first meetings; λ mainly shapes the tail).
6. **Prior alignment bug** (app maps EOF 'next' to the first future step even when that meeting
   has been held since the round): affects ~20% of backtest rows but changes MAE by < 0.2bp
   at λ = 0.005, so it is cosmetic for now; it matters if λ is raised.

**Recommendation:** μ = 0.05 (from 0.25), keep λ = 0.005 (0.02 is equivalent within noise).
Meeting-1 MAE falls from 10.0 / 11.2 / 18.1bp to 8.8 / 9.3 / 14.7bp at h = 1 / 5 / 20, bias
goes from -4.5 / -6.4 / -12.1bp to about +1 / -1 / -5bp, and the 25bp-bucket hit rate rises from
71% to 80% at h = 1. Cumulative-to-meeting-3 error is unchanged. μ ≤ 0.01 adds noise at h = 1-5.

