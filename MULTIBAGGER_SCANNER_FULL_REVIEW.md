# Multibagger Scanner — Full System Review

**Date:** July 12, 2026
**Scanner:** `app/multibagger.py` (V5 architecture)
**Scope:** End-to-end inspection from constituent fetch → price batch download → fundamentals fetch/caching → pipeline scoring → watchlist persistence → alert generation. No code changes; full identification of data gaps and hidden failure modes.

---

## Executive Summary

The Multibagger scanner is a heavier, multi-stage system that combines price/time-series metrics and deep fundamental fetches from Yahoo Finance. The pipeline is complex and includes a local fundamentals cache, parallel fundamentals fetch with a rate-limiter, a custom V5 pipeline (external module), and an exit monitor. The scanner includes many defensive features (cache restore, circuit breakers, rate-limit recording, save-to-DB fallback), but the complexity introduces many failure surfaces.

Top-level findings:
- Critical failure modes: 3 (manifest validation, fundamentals fetch <70%, heavy reliance on Yahoo Finance with circuit break)
- High-impact issues: 8 (cache freshness, tz handling, fallback data semantics, pledge defaulting, partial persistence risk)
- Operational complexity: several moving parts requiring observability (NSE constituent fetch, yfinance bulk download, rate limiter behavior, DB upserts)

---

## Inputs & Upstream Systems

- Constituents: NSE indices via `CONSTITUENT_URLS` (HTTP GET CSV), normalized via `daily_builder.SYMBOL_CORRECTIONS` (lines ~170–209)
- Price time-series: `yf.download(... period="1y", interval="1d")` in `batch_download_market_data()` using chunking, timezone normalization, and stripping forming candle when market open (lines 211–389)
- Fundamentals: `fetch_ticker_fundamentals()` uses `yfinance.Ticker.info/fast_info/financials` with `yf_rate_limiter` (acquire/release, record_rate_limit, CircuitOpenError) (lines 568–731)
- Local fundamentals cache persisted to JSON with Postgres backup (lines 133–166, 170–169)
- Upstream build manifest validation: `database.get_latest_build_manifest(today_str)` (lines 842–851) — scanner aborts if manifest missing
- Pipeline: `core.multibagger_pipeline.run_pipeline_for_symbol()` (used to score and classify)
- Persistence: `save_watchlist_to_db(results)` (bulk `execute_values` upsert) and `save_alert_if_new()` for alert insertion

---

## Walkthrough: Data Path & Key Checks

1. Validate upstream build manifest (lines 842–851). If missing or not SUCCESS/FALLBACK_SUCCESS, scanner aborts and marks health DOWN.
2. Batch download price history for all constituents (lines 211–389). Uses chunk_size=150, re-chunking fallback, timezone normalization, and `strip_forming` logic if market open.
3. Parse price frames into `StockPriceData` with rolling SMAs, ATR, momentum, etc. Requires minimum `len(ticker_df) >= 50` (line 287) — conservative for multibagger needs.
4. Apply cheap filters (price, turnover) and build shortlist; inject open_positions to ensure their fundamentals refreshed (lines 1106–1130).
5. Fetch fundamentals concurrently using `fetch_ticker_fundamentals()` (lines 1134–1183) with `yf_rate_limiter` to protect Yahoo access.
6. Enforce minimum 70% fundamentals fetched vs shortlist (lines 1193–1201) — aborts and marks scanner DOWN if not met.
7. Run V5 pipeline for each candidate `run_pipeline_for_symbol()` and classify/score (lines 1325–1369).
8. For alert candidates, possibly call `save_alert_if_new()` to insert alerts (lines 1494–1508).
9. Bulk write to watchlist table via `save_watchlist_to_db(results)` using `execute_values` upsert (lines 734–783, 1530–1532).

---

## Detailed Issues (Severity, Location, Explanation, Impact, Detection)

### Critical

1. CRIT-MB-01: Upstream manifest gating (lines 842–851)
   - Explanation: Scanner aborts if `get_latest_build_manifest(today_str)` missing or status not successful. This is a hard safety gate to ensure upstream daily builder succeeded.
   - Hidden failure: If manifest check fails due to DB read error (temporary), system aborts despite available data locally.
   - Impact: Entire run aborted leading to missed alerts; but this is intentional for data integrity.
   - Detection: Monitor `upsert_scanner_health("MULTIBAGGER","DOWN",...)` and surface manifest failures.

2. CRIT-MB-02: Fundamentals fetch <70% abort (lines 1193–1201)
   - Explanation: If less than 70% of shortlist fundamentals were fetched, scanner marks DOWN and aborts. This ensures quality but causes many runs to fail when YF rate limits.
   - Hidden failure: Rate limiter circuit (yf_rate_limiter) may open, causing many fetches to fail; scanner aborts.
   - Impact: Missed multibagger alerts for that run.
   - Detection: Track `record_rate_limit` events and `yf_acquire` CircuitOpenError metrics.

3. CRIT-MB-03: Reliance on Yahoo Finance `info` and `financials` (lines 568–731)
   - Explanation: `fetch_ticker_fundamentals()` accesses multiple YF endpoints and fragile `info` fields — missing keys, sign conventions, or API changes cause unpredictable fallback behavior.
   - Hidden failure: When YF blocks requests partially (some info keys missing), fallback logic sometimes returns partial `fund` dict with missing critical metrics; `passes_multibagger_quality_gate` may then reject or misclassify.
   - Impact: False negatives or incorrect classification; or worse, fallback data labeled `FALLBACK` may be treated as live depending on code paths.
   - Detection: Log counts of `fallback` returns and `record_rate_limit` calls.

### High

4. HIGH-MB-01: Timezone normalization & forming bar strip (lines 216–286)
   - Explanation: `batch_download_market_data()` sets index tz_localize(IST) or tz_convert(IST). The code strips the last bar if `is_market_open` and last timestamp equals today. This relies on consistent timezone handling across providers.
   - Hidden failure: If provider returns timestamps in UTC or different naive timezone, the comparison `last_ts.date() == ist_now.date()` may be wrong and cause either unintended removal of final bar or leaving forming candle. That affects SMA/ATR/High calculations.
   - Impact: Off-by-one bar errors leading to inaccurate SMAs and buy zone calculations.
   - Detection: Compare last_ts hour to expected market hour; log unexpected offsets.

5. HIGH-MB-02: `get_cached_fundamentals()` strict tz check (lines 531–541)
   - Explanation: It rejects cache entries with naive timestamps (no tzinfo). This could invalidate perfectly functional cache entries if they were written earlier in legacy format.
   - Impact: Large cache churn and re-fetching fundamentals unnecessarily; may hit rate limits.
   - Detection: Count cache misses due to timezone rejection and track cache versioning.

6. HIGH-MB-03: `promoter_pledge_pct` defaulting to 0.0 (lines 1284–1302)
   - Explanation: If pledge scraper fails, they default to 0.0 to avoid killing stocks. This is a business decision but hides potentially risky stocks when pledge data is missing.
   - Impact: Hidden risk: stocks with high pledge may be misclassified as safe when pledge data fetch fails.
   - Detection: Track `unverified_pledge_count` and set an operator alert when high.

7. HIGH-MB-04: Bulk `execute_values` upsert error handling (lines 734–785)
   - Explanation: `save_watchlist_to_db()` wraps execute_values and logs exceptions but does not implement retry or partial rollback semantics.
   - Hidden failure: On DB transient error (network glitch), the upsert may partially fail or no partial commit; because they use `with conn` and commit after execute_values, the failure path will rollback. But no retry and scanner continues.
   - Impact: Watchlist DB may not be updated; alerts might be generated but watchlist stale.
   - Detection: Count exceptions in `save_watchlist_to_db` and monitor DB availability.

8. HIGH-MB-05: `yf_rate_limiter` interactions & CircuitOpenError (lines 694–707)
   - Explanation: The code calls `yf_acquire` and handles `CircuitOpenError` by aborting fetch for symbol. The rate limiter may identify system-level overuse and open the circuit, causing many fetches to be skipped.
   - Hidden failure: Rate limiter opens due to other scanners; multibagger fetches fail en masse and scanner aborts due to 70% threshold.
   - Detection: Centralized rate-limit health metric; throttle coordination across scanners.

9. HIGH-MB-06: Inconsistent semantics of `data_freshness` (lines 682–684, 711–726)
   - Explanation: Fundamentals fallback returns `data_freshness: 'FALLBACK'` and some logic later rejects or reduces scoring if `data_freshness == 'FALLBACK'` (line 1316). But sometimes fallback `fund` with minimal keys is accepted and used to compute valuations.
   - Impact: Mixed-quality data feed enters pipeline leading to misclassifications.
   - Detection: Track the ratio of `FALLBACK` fundamentals used and the resulting candidate success vs baseline.

10. HIGH-MB-07: Exit monitor uses `yfinance.fast_info.last_price` and may override price (lines 895–901)
    - Explanation: Exit monitor prefers live price by calling YF; if that fails, it uses cached batch price. However, YF may have stale or missing `fast_info.last_price` depending on rate limits.
    - Impact: Inconsistent exit triggers causing unexpected closes or misses.
    - Detection: Log discrepancies between batch price and `last_price` and frequency of overrides.

### Medium / Operational

11. MED-MB-01: Constituents CSV fetching & symbol normalization (lines 170–209)
    - Explanation: Uses NSE archive CSVs. Network failures or format changes (column name changes) can result in missing or malformed symbols. Also note that some symbols contain `&` that must be preserved for yfinance.
    - Impact: Wrong universe selection and possible misses.
    - Detection: Monitor fetched constituent counts per index and validate symbols against daily_builder corrections.

12. MED-MB-02: Minimal row-window constraints (lines 287 & 319–322)
    - Explanation: `batch_download_market_data` requires `len(ticker_df) >= 50` and computes optional 200-day SMA with window fallback to `min(200, len(close_series))`. Short histories may produce degraded metrics.
    - Impact: New listings excluded. But may be intentional.

13. MED-MB-03: Cache save & Postgres upload (lines 151–166)
    - Explanation: On saving cache JSON locally, they attempt `upload_parquet_to_db` as backup; if upload fails they log and continue. This is good but silent failures increase risk of cache loss on environment restarts.
    - Detection: Monitor backup success rate to DB.

14. MED-MB-04: `run_pipeline_for_symbol` dependency black-box risk (multiple lines)
    - Explanation: Multibagger depends heavily on `core.multibagger_pipeline`; if that module changes API or errors unexpectedly, the scanner logs and skips symbols but may produce wrong outputs.
    - Detection: Centralized telemetry for pipeline exceptions.

15. MED-MB-05: Alerts insertion uses `save_alert_if_new` with stop_loss=0.0 (lines 1500–1508)
    - Explanation: Multibagger intentionally posts alerts with no stop loss. This is business logic but needs consumer awareness (position sizing, downstream risk systems) to avoid trades without SL.

---

## Combination Failure Scenarios (Hidden)

A. Rate-limit cascade
- `yf_rate_limiter` opens due to heavy fundamental fetches from multibagger + other scanners → many `fetch_ticker_fundamentals` return None or fallback → fundamentals <70% → scanner marks DOWN. Meanwhile, other scanners might also be affected creating a whole-system data outage.
- Detection: Central rate-limiter 'open' metric and `record_rate_limit` logs.

B. Cache timezone rejection + legacy cache
- Legacy cache written with naive `fetched_at` timestamps gets rejected by `get_cached_fundamentals()` (line 535) → cache miss → fresh fetch → increased load on YF → more rate limits.
- Detection: sudden spike in `fetched_count` and `fetched_from_cache` delta.

C. Pledge scraper failure masking risk
- `fetch_promoter_pledge` fails → default to 0.0 → passes `passes_multibagger_quality_gate` → stock included in watchlist but in reality high pledge → downstream risk event on holdings.
- Detection: high `unverified_pledge_count` and manual reconciliation with NSE pledge filings.

D. Chunked yfinance batch downgrade
- `yf.download` returns DataFrame without MultiIndex and code attempts sub-chunking of size 10; if provider changes structure or returns inconsistent columns, batch parsing may miss many tickers silently.
- Detection: Compare `results` parsed count vs requested chunk size and log downgrades.

E. Partial DB upsert fail
- `save_watchlist_to_db()` fails during execute_values with exception → rollback → no reattempt → watchlist not updated → alerts not reflected in UI
- Detection: track exceptions and implement off-line replays.

---

## Observability & Operational Recommendations (No code changes)

1. Centralized rate-limiter telemetry: expose `yf_rate_limiter` state, record counts of `CircuitOpenError`, `record_rate_limit` invocations.
2. Cache diagnostics: add a cache_version field and log `fetched_at` tz awareness rejections to understand churn.
3. Constituents & Price fetch metrics: chunk success rate, chunk sizes, downgrading occurrences, parsed result counts.
4. Pledge fetch audit: expose `unverified_pledge_count` and set alert if >X% of shortlist lacks pledge data.
5. Post-run verification: compare number of `save_watchlist_to_db` rows vs results length; create scheduled retry jobs for failed upserts.
6. Exit monitor safety: add a secondary live-price verification service or fallback price source to avoid depending solely on YF fast_info for exits.

---

## Appendix: Key Line References

- Constituents fetch & normalization: lines 170–210
- Batch price download & parse: lines 211–389
- Fundamentals fetch & rate limit logic: lines 568–731
- Cache load/save: lines 133–166, 1180–1187
- 70% fundamentals check abort: lines 1193–1201
- Pipeline invocation: line 1326
- Alert save `save_alert_if_new`: lines 1494–1508
- Bulk watchlist upsert: lines 734–783, 1530–1532

---

End of `MULTIBAGGER` scanner full review.

---

## Addendum — Cross-Scanner Verification (2026-07-12, confirmed by reading source)

### CONF-MB-1 (High): Rate-limiter is shared; cache is NOT (the cascade is real)
- MULTIBAGGER fetches prices via its **own** `yf.download(...)` in `batch_download_market_data()` (`multibagger.py:228`) — it does **not** go through `price_cache.fetch_watchlist_data`, so it does **not** share the `price_cache._fetch_lock` cache with REVERSAL/EOD/MULTI_TF.
- However it **does** share `yf_rate_limiter` (`yf_acquire`/`yf_release` at `multibagger.py:113`, `:577`). So the rate-limit cascade described in HIGH-MB-05 / combo A is genuine and cross-scanner: a limiter trip during MULTIBAGGER fundamentals fetch (`:1145` ThreadPool of 5) can open the circuit and also starve the nightly EOD/REVERSAL fetches.
- Detection: central `record_rate_limit` count + correlate `MULTIBAGGER DOWN` with `EOD`/`REVERSAL` DOWN events.

### CONF-MB-2 (Medium): DB-level stale/fallback guard is dead code (all 4 scanners)
- `save_alert_if_new._is_stale_buy()` (`database.py:1134`) only triggers when the caller passes `used_fallback_data` / `data_quality` in `context`/`kwargs`. MULTIBAGGER does not pass those. Note MULTIBAGGER *does* guard FALLBACK itself: `raw_fundamentals.get("data_freshness")=="FALLBACK"` is rejected at the PRE_GATE (`multibagger.py:1327`) and the exit monitor skips fundamental exits on FALLBACK (`multibagger.py:970-981`). So the main scan already prevents FALLBACK-driven alerts; the DB guard is redundant-dead.

### CONF-MB-3 (Low/positive): de-dup for MULTIBAGGER is correct
- Unlike REVERSAL (`:587`) and EOD (`:569`), MULTIBAGGER does **not** use the broken `(symbol, dedup_key)` in-memory check. It relies on (a) `save_alert_if_new` DB `ON CONFLICT (symbol, breakout_type, scanner, alert_date)` and (b) an explicit `open_symbols` skip (`multibagger.py:1389`) so an already-open MULTIBAGGER position is not re-alerted. This is the correct pattern.

### CONF-MB-4 (Medium): "No SL" is by design but unmonitored downstream
- `save_alert_if_new(..., stop_loss=0.0, target_price=0.0, ...)` (`multibagger.py:1505-1519`) posts alerts with no stop/target. Confirmed intentional. Operators/risk systems must be aware these positions have no automated SL from the scanner; only the exit monitor (200-DMA / 25% drawdown / quality-gate) closes them.

### CONF-MB-5 (Low): regime default mismatch vs siblings
- MULTIBAGGER defaults `market_regime="BEAR"` (`multibagger.py:1216`) and only flips to BULL if `^NSEI` close > 200-SMA. REVERSAL/EOD compute regime from `get_macro_regime(nifty_ret)` and default to `NEUTRAL` on failure. So a Nifty fetch failure makes MULTIBAGGER conservatively BEAR while EOD/REVERSAL go NEUTRAL — slightly inconsistent conservative posture across scanners. No false alerts, but worth noting for aligned behavior.
