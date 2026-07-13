# Reversal Scanner — Full System Review

**Date:** July 12, 2026
**Scanner:** `app/reversal_scanner.py` (v6)
**Scope:** End-to-end analysis from upstream data sources (watchlist, price, delivery, macro) to alert persistence. No code changes — identification of every gap, failure mode, and hidden interaction that can affect alert quality.

---

## Executive Summary

The `REVERSAL` scanner is designed to detect high-quality mean-reversion opportunities using a nightly window (18:30–23:59 IST). Overall design is robust and conservative: multiple quality filters (price/lists/RSI/MACD/SMA/volume), explicit cooldowns, and double-checks for stale data. However, I found several critical and high-impact gaps that can silently suppress or corrupt alert generation under realistic failure modes (API rate limits, stale fallback cache, DB errors, timezone issues, and missing delivery/bhavcopy data).

Key results:
- Critical issues that can abort the run or produce no alerts: 2
- High-impact issues that can suppress valid alerts or generate false negatives: 7
- Medium/operational issues (logging/monitoring/enrichment): 6

This report enumerates each issue with exact file/line references, causal scenarios, likely impact, and non-invasive mitigations (monitoring, restart policies, manual operator checks). No code edits are made.

---

## Inputs & Upstream Systems

Sources used by `reversal_scanner.py`:
- Watchlist: `get_watchlist()` (from `app/watchlist_cache.py`) — parquet file fallback to Postgres
- Price history: `fetch_watchlist_data(watchlist, period="1y", interval="1d")` (from `app/price_cache.py`) — global fetch lock, disk cache, fallback stale data with `df.attrs['is_stale']`
- Delivery data: `fetch_previous_day_delivery()` (from `delivery_data.py`)
- Macro regime: `get_nifty_20d_return()` and `get_macro_regime()` (from `macro_utils.py`)
- Push/persistence: `database.save_alert_if_new`, `upsert_fetch_error`, `upsert_scanner_health`, `verify_alerts_saved_today`

Indicator computation: `apply_indicators(df, timeframe="1d")` (from `app/technical_indicators.py`) — creates RSI, EMA20, SMA50, SMA200, MACD, ATR, SWING_LOW/HIGH, VWAP, etc.

Minimum internal data expectations:
- Price history length >= 100 bars (explicit check in code)
- Non-empty watchlist
- Fresh (non-stale) price frames (scanner rejects if df.attrs['is_stale'])

---

## Walkthrough: Data Path & Critical Checks (with line refs)

1. Watchlist load (lines 268–275):
   - `watchlist = get_watchlist()` → if empty: early exit, upserts scanner health to OK with 0 alerts (lines 273–281)
   - Impact: If watchlist cache missing (parquet + DB restore fails) the scanner exits cleanly with no alerts — operator may not immediately notice.

2. Price fetch (lines 283–296):
   - `all_ticker_data = fetch_watchlist_data(..., period="1y", interval="1d")` and enforces >=70% fetched symbols
   - On <70% fetched: updates scanner health to DOWN and raises an exception to abort (lines 287–295)

3. Per-symbol loop (lines 350–786):
   - Key rejects: no data, stale data (df.attrs['is_stale']), insufficient bars (<100), missing indicator columns, low price, low liquidity (avg_vol_20d), drop band between 15/20–MAX
   - Calls `apply_indicators(ticker, timeframe="1d")` (line 387)
   - Checks for required indicator columns (line 392–396)
   - RSI curl checks and MACD cross checks (lines 460–565)
   - SL & target computed via `compute_sl_and_target(..., mode="REVERSAL")` (lines 621–647), requires ATR and many indicator columns
   - Final scoring `_score_reversal()` and threshold check (line 685)

4. Persistence (lines 820–862):
   - `database.save_alert_if_new(...)` used to persist shortlisted alerts
   - A verify step `verify_alerts_saved_today()` ensures DB saved expected count (lines 846–852)
   - Final scanner health upsert (lines 855–862)

---

## Detailed Issues (Severity, Location, Explanation, Impact, Detection)

### Critical Issues (must monitor / operator action)

1. CRIT-REV-01: "Watchlist missing → silent OK" (lines 268–281)
   - Explanation: If `get_watchlist()` returns empty (parquet missing and DB restore failed), the scanner returns 0 and upserts scanner health with status OK and total_count=0. This hides failure of upstream daily builder.
   - Impact: Entire night of scanning produces no alerts; operators may not be alerted to upstream job failure.
   - Detection: Add monitoring around the presence of watchlist parquet or set scanner health to DEGRADED instead of OK when watchlist empty. (Recommendation only — no code change.)

2. CRIT-REV-02: "Price fetch <70% abort raises exception" (lines 283–295)
   - Explanation: Good defensive behavior — aborts when provider returns <70% symbols. But this is thrown as exception which bubble-ups and sets scanner health DOWN. Without alert routing, main scheduler may not retry appropriately.
   - Hidden scenario: If fetch intermittently returns 65% then a legitimate scan is aborted repeating for the night.
   - Impact: Missed alerts  — conservative but causes missed opportunities.
   - Detection: Monitor `upsert_scanner_health` DOWN events with this specific error message.

### High Issues (suppress/skip valid setups)

3. HIGH-REV-01: Strict bar-count minimum (len < 100) (lines 382–385)
   - Explanation: The scanner requires at least 100 daily bars (approx. 100 trading days). This blocks newly listed stocks or ADR-like listings with limited history.
   - Impact: Potentially high-quality short-history names are not evaluated.
   - Detection: Log counts of `insufficient_bars` rejection per run and compare with expected universe.

4. HIGH-REV-02: Stale data rejection early (lines 372–376)
   - Explanation: If `ticker.attrs['is_stale']` set by `price_cache` fallback, scanner rejects symbol immediately. This is safe, but combined with watchlist returning <70% and fallback caching, many symbols will be rejected.
   - Hidden failure: Stale flag might be set even for partial fresh data if fetch failed in the batch and fallback cache was used. This reduces universe silently.
   - Impact: Valid reversal setups suppressed at market-open or during rate-limit episodes.
   - Detection: Record `stale_data` counter and if >10% of watchlist, raise alert.

5. HIGH-REV-03: Delivery data availability fallback (lines 256–260)
   - Explanation: If `fetch_previous_day_delivery()` fails, `prev_delivery_map` becomes empty and delivery bonuses are not applied during scoring.
   - Impact: Reversal signals depending on institutional delivery are scored lower — some alerts drop below threshold.
   - Detection: Track `prev_delivery_map` size and log when zero.

6. HIGH-REV-04: MACD cross evaluation windows and NaNs (lines 551–563)
   - Explanation: MACD bullish cross search uses last 10 bars and expects MACD and MACD_SIGNAL columns. If indicators had NaN (insufficient bars), symbol rejected. Combined with 100-bar minimum, this is unlikely but can happen after timezone or fetching errors.
   - Impact: False negatives when indicators couldn't compute due to partial data.
   - Detection: Count `no_macd_cross` and `indicator fail` over runs.

7. HIGH-REV-05: SL/target requires ATR and many columns (lines 621–647)
   - Explanation: `compute_sl_and_target` consumes ATR, SWING_LOW/HIGH, BBs, S1/S2, VWAP etc. If any are missing/NaN, `sl_result` uses fallbacks but may produce low R:R leading to rejection (lines 651–653).
   - Impact: Conservative rejection due to missing technical context; valid setups suppressed.
   - Detection: Log cases where `sl_result` fallback was used or `rr_ratio` below threshold.

8. HIGH-REV-06: Failed-reversal cooldown depends on external DB function (lines 221–230, 360–365)
   - Explanation: `_is_symbol_in_reversal_cooldown()` imports `database.is_symbol_in_failed_reversal_cooldown`. If the DB function missing/raises, they log and treat as not in cooldown.
   - Hidden scenario: DB function exists but DB down — cooldown check may raise exceptions causing inconsistent behavior.
   - Impact: Risk of re-alerting failed tickers (leak) if DB can't be read.
   - Detection: Monitor exceptions in this helper (they log warning).

### Medium/Operational Issues

9. MED-REV-01: Dedup key semantics & cooldown_alerts set (lines 576–583)
   - Explanation: Dedup key assembled as `{category}|{symbol}|{today_str}|{breakout_type}`. `cooldown_alerts` is a set from `get_recent_alerts_for_scanner()` but caller checks `(symbol, dedup_key) in cooldown_alerts` — ensure shapes match.
   - Impact: If `get_recent_alerts_for_scanner()` returns tuples `(symbol, breakout_type)` (see DB function earlier), the membership check may never match, allowing duplicates.
   - Detection: Spot duplicate alerts in a single day by counting identical `dedup_key` writes.

10. MED-REV-02: Export CSV idempotency (lines 326–337, 750–785)
    - Explanation: Exporting CSV and attempting idempotent removal of today's rows is fragile when CSV mutated by external processes. Failure logged but scanner continues.
    - Impact: Exports may have duplicates across restarts.
    - Detection: Monitor export file size and dedupe by script.

11. MED-REV-03: Heavy reliance on `verify_alerts_saved_today()` (lines 846–852)
    - Explanation: If verify fails intermittently due to DB latency, scanner marks DOWN even though `save_alert_if_new()` may have inserted some rows.
    - Impact: False negatives on scanner health reporting. Operators may misinterpret.
    - Detection: Cross-check DB directly for recent alert rows when health flips to DOWN.

12. MED-REV-04: Use of `row` watchlist fields (ROE/YOY) with type coercion (lines 442–456)
    - Explanation: `row.get("ROE %")` might be string, NaN, or missing. They guard with try/except; still, inconsistent watchlist formatting could reject many candidates.
    - Impact: Mis-specified watchlist columns lead to more `fundamental_filter` rejections.
    - Detection: Monitor `fundamental_filter` rejection counts and sample `row` values for anomalies.

13. MED-REV-05: Inconsistent timezone handling for timestamps in `apply_indicators` input (lines 378–381; `apply_indicators` uses timezone normalization internally)
    - Explanation: The code converts multiindex columns and drops NaNs; mismatches between naive timestamps and tz-aware ones may cause off-by-one bar errors.
    - Impact: Edge cases near daily boundary may strip current bar incorrectly.
    - Detection: Spot off-by-one in last bar timestamp vs. exchange closing time for failing stocks.

14. MED-REV-06: Logging granularity for rejection reasons (lines 303 onward) is good but some counters are grouped; need better per-symbol reason logs for auditing.
    - Recommendation: Dump shortlist reasons JSONL (already partially done for export) for forensic analysis.

---

## Combination Failure Scenarios (Hidden Bugs)

I exercised multiple realistic combinations to surface hidden failure modes. For each, I state cause → effect → detection:

A. Rate limit + hourly deployment overlap
- Cause: `fetch_watchlist_data` returns 40% fresh + many cached frames flagged `is_stale`.
- Effect: `fetched_count < 70%` → scanner raises exception and aborts. No alerts tonight.
- Detection: Repeated UPDATES to scanner health "DOWN" with "STALE DATA/INCOMPLETE DATA ERROR" message.

B. Watchlist parquet missing + DB restore failing (network outage)
- Cause: `get_watchlist()` returns empty; scanner exits returning 0 but health set to OK.
- Effect: Silent failure (no alerts). Operator lacks alarm because status OK.
- Detection: Monitor watchlist file presence and an explicit "WATCHLIST MISSING" metric.

C. Delivery data missing + stale price fallbacks present
- Cause: `fetch_previous_day_delivery()` fails and `all_ticker_data` partly stale
- Effect: Delivery bonus absent, stale frames cause symbol rejection; low scoring → fewer alerts
- Detection: `stale_data` and `no_delivery` counts high; alerts significantly below baseline.

D. DB write partially failing during persistence (network timeout)
- Cause: `save_alert_if_new()` fails for some symbols, `verify_alerts_saved_today()` detects mismatch
- Effect: Scanner flags DOWN; missing alerts; possible duplicates if retry logic re-runs
- Detection: DB alert rowcounts vs. in-memory shortlisted_alerts mismatch; many `upsert_fetch_error` logs.

E. Local timezone mismatch on price cache
- Cause: Source returns naive timestamps or tz mismatch; `apply_indicators` normalizes but strip_forming_candle may mis-evaluate forming bar
- Effect: Wrong last bar used → RSI/EMA computed on wrong data → false rejection or acceptance
- Detection: Compare last bar timestamp in `ticker` vs. expected exchange close time.

---

## Observability & Operational Recommendations (No code-change, operational)

1. Add dashboard metrics (Prometheus / internal logs) for:
   - watchlist file age & existence
   - fetch success rate (fetched_count/len(watchlist)) per run
   - stale_data % (per run)
   - counts of each rejection reason (already present) and top 20 rejected symbols
   - delivery_map size
   - verify_alerts_saved_today mismatches

2. Alerting rules (Ops):
   - If watchlist file missing → Pager duty immediate
   - If fetch rate < 70% for 2 consecutive runs → Pager duty
   - If `verify_alerts_saved_today()` fails → Pager duty

3. Add nightly sanity checks (automation / external script):
   - Compare this run's shortlisted_alerts with previous 7 days to detect anomalies
   - Export per-symbol rejection reasons to S3/DB for forensic review

4. Runbook items for operators:
   - How to restore watchlist from DB (commands to call `download_parquet_from_db`) and restart scanner
   - How to interpret `upsert_scanner_health` content and perform emergency manual scans

---

## Final Risk Matrix (one-line remediation)

- Watchlist missing → High risk (monitor file + alert)  
- Price fetch partial/stale → High risk (health=DOWN already — monitor and retry)  
- Delivery missing → Medium risk (affects scoring)  
- DB persist/verify mismatches → High risk (investigate DB connectivity)  
- Indicator NaNs due to insufficient bars → Medium risk (log & monitor)  

---

## Appendix: Important Line References

- Watchlist load: lines 268–275
- Price fetch & 70% abort: lines 283–296
- Per-symbol processing: lines 350–786
- Indicators call: line 387
- SL/Target compute: lines 621–647
- Persistence: lines 820–862


---

End of `REVERSAL` scanner full review.

---

## Addendum — Cross-Scanner Verification (2026-07-12, confirmed by reading source)

### CONF-REV-1 (High): In-memory dedup key shape mismatch (MED-REV-01, now confirmed against DB)
- `reversal_scanner.py:587` tests `(symbol, dedup_key) in cooldown_alerts` where `dedup_key = f"{category}|{symbol}|{today_str}|REVERSAL"`.
- `get_recent_alerts_for_scanner("REVERSAL", ...)` returns `set[tuple[str,str]]` of `(symbol, breakout_type)` (`database.py:1297`) — the second element is the **breakout_type** (`"REVERSAL"`), not the full dedup string.
- Therefore the membership test can never match → the in-memory dedup at `:587` is **dead code**. True same-day de-dup is still enforced by `save_alert_if_new`'s `ON CONFLICT (symbol, breakout_type, scanner, alert_date)` (`database.py:1201`), so no false negatives occur, but the `cooldown`/early-skip optimization is ineffective and the `duplicate` counter from this path never fires. (EOD has the identical bug at `eod_scanner.py:569`; MULTI_TF avoids it by using `check_recent_alert()`.)

### CONF-REV-2 (Medium): DB-level stale/fallback guard is dead code (all 4 scanners)
- `save_alert_if_new._is_stale_buy()` (`database.py:1134`) only triggers when the caller passes `used_fallback_data` / `data_quality` in `context`/`kwargs`. No scanner passes those flags, so the guard never fires. Stale-buy protection depends solely on the scanner's own `df.attrs['is_stale']` checks (REVERSAL does this at `:378`).

### CONF-REV-3 (Medium): Shared global fetch lock & rate limiter
- All scanners serialize on `price_cache._fetch_lock` and share `yf_rate_limiter`. REVERSAL (nightly) and MULTI_TF (intraday) are usually non-overlapping, but a forced/manual REVERSAL run during market hours can block behind MULTI_TF intraday fetches. A single rate-limit event can abort multiple scanners.

### CONF-REV-4 (Low): `failed-reversal cooldown` is the *real* dedup that works
- Unlike the dead in-memory dedup, `_is_symbol_in_failed_reversal_cooldown()` (`database.py:987`) queries the DB directly and only suppresses symbols whose **most recent REVERSAL alert is a LOSS within `cooldown_days` business days**. This is the effective re-alert guard and is functioning. Note it does NOT suppress on WIN/OPEN/CLOSED, so a winning reversal can re-alert the next day (by design).
