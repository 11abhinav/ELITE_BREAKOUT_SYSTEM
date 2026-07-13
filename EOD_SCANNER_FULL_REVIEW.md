# EOD Scanner — Full System Review

**Date:** July 12, 2026
**Scanner:** `app/eod_scanner.py`
**Scope:** End-to-end analysis from watchlist through price fetch, indicator application, breakout detection (`breakout_engine.detect_breakouts`), scoring (`scoring_engine.calculate_score`) and alert persistence (`save_alert_if_new`). No code changes — analysis only.

---

## Executive Summary

`EOD` scanner is the primary daily breakout detector. It is executed in a nightly window and targets structurally robust breakout setups. The scanner is careful about data freshness, requiring at least 70% of the watchlist to be fetched before proceeding, enforces a 200-bar minimum for indicators, validates timestamps, and runs a pairing of delivery and prices fetches concurrently with dynamic timeouts.

Despite solid architecture, I identified several weaknesses and hidden interaction modes that can (a) abort the run entirely (rate limits/timeouts), (b) silently reduce coverage (stale fallbacks), or (c) bias results (R:R threshold, BB_PERCENTILE NaNs, OBV penalty). The scanner does include health updates and explicit delete/verify steps to ensure idempotency, which reduces data-corruption risk.

Summary of findings:
- Critical aborts on partial fetch (<70%) — intentional but causes missed opportunities
- Multiple hidden false-negative modes due to indicator NaNs, BB percentile edge-cases, and stale fallbacks
- A small number of operational pitfalls around thread management, timeout tuning, and export/persistence that can be improved via monitoring

---

## Inputs & Upstream Systems

- Watchlist: `get_watchlist()` (parquet, DB restore fallback)
- Delivery bhavcopy: `fetch_delivery_data(ist_now.date())` or fallback to `fetch_previous_day_delivery()`
- Price history: `fetch_watchlist_data(watchlist, period="2y", interval="1d")` executed concurrently with `fetch_delivery_data`
- Indicator computation: `apply_indicators(..., timeframe='1d')`
- Breakout detection: `detect_breakouts(ticker, timeframe='1d')` (from `breakout_engine.py`)
- Scoring: `calculate_score(...)` (from `scoring_engine.py`) — returns score, model_version, bayesian_weights
- Persistence: `save_alert_if_new(...)` and `verify_alerts_saved_today()`

Important configuration values from `config` used by EOD:
- `MIN_SIGNALS`, `MIN_BODY_RATIO`, `MIN_CLOSE_POSITION`, `MIN_VOLUME_RATIO`, `SCORE_THRESHOLDS`, `EOD_ADVANCED_CONFIG`, `ALERT_COOLDOWN_MINUTES`, `MIN_STOCK_PRICE`, `ADX_MIN_THRESHOLD`

---

## Walkthrough: Data Flow (with line refs)

1. Acquire watchlist (lines ~96–103). If empty, exit cleanly with OK.
2. Concurrent fetch: `fetch_delivery_data()` and `fetch_watchlist_data(..., "2y", "1d")` using ThreadPoolExecutor (lines 131–149). Dynamic timeout computed based on watchlist size (min 180s, 1.5s per symbol).
3. If `fetched_count < 70%`: abort by raising exception and updating scanner health to DOWN (lines 160–168).
4. Idempotent DB cleanup: delete today's alerts for EOD before new inserts (lines 174–189).
5. For each symbol: validate columns (Open/High/Low/Close/Volume), dropna, ensure len >= 200 (lines 267–292).
6. Call `apply_indicators(...)` (line 292) then `detect_breakouts(...)` (line 298).
7. Signal gate checks: body_ratio, close_position, volume_ratio, ATR expansion, BB width percentile (lines 374–445), many configurable thresholds in `EOD_ADVANCED_CONFIG`.
8. Scoring via `calculate_score(...)` and threshold check (lines 532–561). R:R check via `compute_sl_and_target` (lines 571–600).
9. `save_alert_if_new()` persistence and duplication handling (lines 639–657). Finally, verify saved alerts (lines 718–727) and upsert scanner health (lines 745–756).

---

## Detailed Issues (Severity, Location, Explanation, Impact, Detection)

### Critical

1. CRIT-EOD-01: Fetch concurrency timeout and 70% threshold (lines 131–168)
   - Explanation: Concurrent fetch uses a timeout scaled to watchlist size; if either delivery or price fetch exceeds timeout or returns <70% symbols, the scan aborts with exception and scanner health DOWN.
   - Hidden failure: If price provider is rate-limited for only one subset of symbols repeatedly (e.g., certain exchanges), scanner will abort even though many valid symbols exist.
   - Impact: Full-night missed run. Conservative by design but operationally costly.
   - Detection: Monitor `upsert_scanner_health` DOWN with message "STALE DATA/INCOMPLETE DATA ERROR".

2. CRIT-EOD-02: Idempotent delete may remove previously-saved alerts before verifying fresh fetch (lines 174–189)
   - Explanation: The code deletes today's alerts only after fetch sufficiency checks succeed (good). But if delete is successful yet later verification fails (DB error while persisting new alerts), we may lose prior alerts for the day. The code raises then marks DOWN. The system handles this, but the window exists.
   - Impact: Possible temporary data loss of today's alerts if crash occurs between delete and new save. Verified step attempts to catch this.
   - Detection: DB audit logs and backups.

### High

3. HIGH-EOD-01: BB_WIDTH_PCTILE NaN behavior (lines 438–441, earlier in indicators)
   - Explanation: `BB_WIDTH_PCTILE` uses rolling 100-window with min_periods=50. For tickers with <50 usable bars, column is NaN; code handles by skipping or defaulting. However, EOD checks `bb_width_pctile > MAX_BB_WIDTH_PCTILE` and may skip if NaN-handling produces unwanted path.
   - Impact: Newly listed or small-history stocks are skipped.
   - Detection: Reject counts for `base_too_wide` and logging of NaN presence.

4. HIGH-EOD-02: Volume ratio mean computation uses `iloc[-21:-1]` (line 352)
   - Explanation: Using `iloc[-21:-1]` ignores the immediate prior bar, producing slight biases and breaking when len < 22.
   - Impact: Small mis-computation of volume_ratio leading to marginal rejections of low-volume thresholds.
   - Detection: Record volume_ratio distribution; see spikes.

5. HIGH-EOD-03: Stale-data check relies on bar timestamp logic (lines 314–345)
   - Explanation: Several branches try to guard stale data via `Date` or `Datetime` column or DatetimeIndex, with timezone localization assumptions. If provider returns naive timestamps, code treats them as IST and localizes — this is correct but depends heavily on correct provider behavior.
   - Hidden failure: If provider changes timestamp semantics (e.g., UTC naive), we can miscompute `_bar_age_days` and reject valid symbols.
   - Detection: Compare bar timestamp to known exchange close times.

6. HIGH-EOD-04: R:R gate applied late (lines 598–600)
   - Explanation: After expensive score computation and candidate acceptance, we compute `sl_result` and reject if `rr_ratio < 1.5`. This can remove otherwise technically valid signals.
   - Impact: Bias toward high volatility setups; penalizes low-volatility but structurally valid breakouts.
   - Detection: Count `low_rr` rejections and examine excluded tickers.

7. HIGH-EOD-05: OBV penalty used as soft penalty (lines 519–526)
   - Explanation: OBV slope <= MIN_OBV_SLOPE applies -5 penalty only after scoring. While reasonable, small negative slopes due to indexing anomalies can unfairly penalize.
   - Impact: Slightly fewer alerts; hidden when OBV slope computed on small windows.
   - Detection: Track OBV_SLOPE distributions.

### Medium/Operational

8. MED-EOD-01: ThreadPoolExecutor shutdown wait=False tuning (lines 131–156)
   - Explanation: The code uses `pool.shutdown(wait=False)` in finally blocks to avoid hanging. If futures are still running and we early-abort, some network fetches may be left incomplete; but thread pool cleanup is handled. This is acceptable but requires monitoring for leftover threads.
   - Detection: Monitor `threading.active_count()` which is already logged (line 732–734)

9. MED-EOD-02: Delivery fallback to previous day (lines 198–201)
   - Explanation: If bhavcopy not available, scanner falls back — reduces fidelity for delivery-based scoring but safer than aborting.
   - Detection: Monitor `delivery_map` size and log fallback occurrences.

10. MED-EOD-03: use of `get_recent_alerts_for_scanner` after delete (line 194)
    - Explanation: They fetch cooldown alerts AFTER deleting today's rows to reflect clean state. Good practice — but ensure underlying DB transaction isolation doesn't show deleted rows to the same session; they use new connection.
    - Detection: DB transaction logs and tests for isolation.

11. MED-EOD-04: Complex rejection counters (line 211 onwards)
    - Explanation: Good observability via `rejection_counts`. Suggest capturing detailed per-symbol reasons to persistent log for audits.

12. MED-EOD-05: Aggregation bias for `SCORE_THRESHOLDS` per regime (lines 225–236)
    - Explanation: Threshold bumped in BEAR regime by +5. Good, but sensitivity may be too coarse; consider dynamic scaling of score based on sector/volatility.

---

## Combination Failure Modes (Hidden)

1. Timeout + Partial Fetch: If `future_delivery` wins but `future_prices` times out, the scanner raises a TimeoutError which aborts the run. Mitigation: Ensure sufficient fetch timeout and stagger fetch windows across scanners.

2. Partial Fetch + Delete Race: In rare sequence where fetch succeeded but DB delete executed and then DB connection times out on persistence, verify step will catch but there is a small risk of short-lived data loss. Runbook should include verifying last saved alerts from DB backups.

3. NaN percentile & ATR missing: If `BB_WIDTH_PCTILE` or `ATR20` are NaN due to insufficient/history problems, the scanner rejects candidate — multiple small failing conditions amplify each other to produce many rejections.

4. Provider timestamp shift (UTC vs. IST): If provider switches from returning naive IST to naive UTC, the stale-data check may reject valid symbols near market boundaries. Detection: spike in `stale_data` rejections concentrated around boundary times.

---

## Observability & Operational Recommendations (No code changes)

1. Add automated run-time monitors:
   - `fetched_count/len(watchlist)` trend line
   - `rejection_counts` per run persisted to time-series DB
   - `verify_alerts_saved_today()` mismatches
   - `delivery_map` size & fallback occurrences

2. Alerting rules:
   - If `fetched_count/len(watchlist) < 0.70` for 2 consecutive runs → Pager
   - If `stale_data` > 10% of watchlist → Warning
   - If `low_rr` rejections spike → Investigate scoring behavior

3. Nightly automated verification:
   - Re-run `detect_breakouts` on a small canonical test list to ensure algorithm changes or provider changes didn't break detection

4. CI / Unit checks for `detect_breakouts` & `calculate_score` using synthetic data to ensure BB/ATR/OBV methods handle small history windows gracefully.

---

## Appendix: Key Line References

- Concurrent fetch and timeout: lines 131–156
- Fetch sufficiency 70% abort: lines 160–168
- Delete today's alerts (idempotency): lines 174–189
- Stale-data checks / timestamp normalization: lines 314–346
- Indicator & breakout detection: lines 292–304, 298
- Scoring & SL/target gate: lines 532–600
- Save alert call: lines 639–657
- Verify saved alerts: lines 718–727

---

End of `EOD` scanner full review.

---

## Addendum — Cross-Scanner Verification (2026-07-12, confirmed by reading source)

The following findings were verified end-to-end against `app/database.py` and the sibling scanners during the consolidated 4-scanner review. They apply to EOD and should be triaged alongside the issues above.

### CONF-EOD-1 (High): In-memory dedup key shape mismatch — dead code (`:569`)
- `EOD` builds `dedup_key = f"{category}|{signal_str}|{today_str}|EOD"` and tests `(symbol, dedup_key) in cooldown_alerts`.
- But `get_recent_alerts_for_scanner("EOD", ...)` returns a `set[tuple[str,str]]` of `(symbol, breakout_type)` — see `database.py:1297`. The second element it stores is the **breakout_type** (`"EOD"`), not the full `dedup_key` string.
- Consequence: `(symbol, dedup_key_string)` can **never** equal `(symbol, "EOD")`, so the in-memory `cooldown_alerts` check at `:569` is **dead code** — `rejection_counts["duplicate"]` from this path never increments. The real de-dup is the DB `ON CONFLICT (symbol, breakout_type, scanner, alert_date) DO NOTHING` inside `save_alert_if_new` (`database.py:1201`), which still prevents true same-day duplicates. So the bug is *silent* (no false negatives in practice) but the intended cheap early-skip is not happening and the `duplicate` counter is misleading. (REVERSAL has the identical bug at `reversal_scanner.py:587`; MULTI_TF does NOT — it uses `check_recent_alert()` correctly.)

### CONF-EOD-2 (Medium): DB-level stale/fallback guard is dead code for all 4 scanners
- `save_alert_if_new._is_stale_buy()` (`database.py:1134`) only suppresses when the caller passes `used_fallback_data` / `data_quality` in `context`/`kwargs`. None of the four scanners populate those flags, so the guard never fires. Stale-alert protection relies entirely on each scanner's own `df.attrs['is_stale']` checks (EOD does this at `:258`). Operators should not assume the DB layer blocks stale buys.

### CONF-EOD-3 (Medium): Shared global fetch lock & rate limiter
- All scanners serialize on `price_cache._fetch_lock` and share `yf_rate_limiter`. EOD runs nightly (18:30–23:59) and MULTI_TF runs intraday, so they are usually non-overlapping; but a manual/forced EOD run during market hours can block behind MULTI_TF intraday fetches (and vice-versa). A rate-limit trip on one scanner cascades to the others.

### CONF-EOD-4 (Low/confirmed): volume-ratio window already noted
- `avg_volume = ticker["Volume"].iloc[-21:-1].mean()` at `:356` (20 bars, excludes the latest) is consistent with REVERSAL (`:432`) and MULTI_TF (`:398`) — intentional "trailing 20d mean vs today" denominator. Not a bug, but noted for consistency across scanners.
