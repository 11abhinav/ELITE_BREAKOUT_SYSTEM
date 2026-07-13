# Multi-TF Scanner (mf) — Full System Review

**Date:** July 12, 2026
**Scanner:** `app/multi_tf_scanner.py` (state-machine ladder: 1h → 30m → 15m → 5m)
**Scope:** End-to-end analysis from upstream data sources (watchlist, multi-timeframe price/fetcher, indicator engine, breakout-watchlist store) to intraday alert persistence. No code changes — identification of every gap, failure mode, and hidden interaction that can affect alert quality.

---

## Executive Summary

The `MULTI_TF` ("mf") scanner is fundamentally different from REVERSAL / EOD / MULTIBAGGER: it is an **always-on, intraday state machine** (runs every 5 minutes while the market is open) that walks a stock up a 4-rung ladder — `HOURLY_APPROVED → SETUP_ARMED → ENTRY_READY → TRADE_ACTIVE` — using 1h, 30m, 15m and 5m candles. It does **not** use `detect_breakouts()` / `scoring_engine()`; it has bespoke ladder logic and stores state in the `breakout_watchlist` table, emitting a single `INTRADAY` alert on the final 5m thrust/pullback trigger.

Because it runs during market hours and leans entirely on **intraday (sub-daily) candles**, its biggest exposure is data-source dependency, not config thresholds. The other three scanners use daily candles (robust via yfinance + disk cache); `MULTI_TF` depends on intraday history that, depending on the configured provider, may be shallow, timezone-mismatched, or unavailable — and the failure modes are **silent** (health stays OK/IDLE, alerts simply don't fire).

Findings:
- Critical issues (silent zero-alert / crash): 3
- High-impact issues (suppress valid triggers / false triggers): 7
- Medium/operational: 6

A notable positive: unlike REVERSAL and EOD, this scanner's de-duplication uses `check_recent_alert()` against the DB correctly, so the "dead dedup key" bug found in the other two scanners is **not** present here.

---

## Inputs & Upstream Systems

- Watchlist: `get_watchlist()` (parquet + Postgres restore) — `multi_tf_scanner.py:81`
- Multi-TF price history: `fetch_watchlist_data(watchlist, period, interval)` from `price_cache`, which routes through `data_provider.get_fetcher()`:
  - Phase A: `(1h, 60d)`
  - Phase B: `(30m, 1mo)`
  - Phase C: `(15m, 5d)`
  - Phase D: `(5m, 1mo)`
  - Provider = `AutoSwitchingFetcher` → **Fyers primary** (if `FYERS_CLIENT_ID`/`SECRET_KEY` + valid token) → **YFinance fallback**. `data_provider.get_fetcher()` returns this.
- Indicator engine: `apply_indicators(df, timeframe="1h"/"30m"/"15m"/"5m")` — computes EMA9/20/50/200, SMA50/200, ADX, BB_WIDTH_PCTILE, PRIOR_20D_HIGH (timeframe-specific rolling windows), SWING levels, daily-reset VWAP for intraday.
- State store (DB): `upsert_breakout_watchlist`, `get_active_breakout_watchlist`, `sweep_stale_breakout_watchlist`, `mark_breakout_watchlist_cooldown` (`database.py:3615+`).
- Macro: `get_nifty_20d_return()` / `get_macro_regime()` — used only to tag the alert's `bayesian_regime`; **no threshold gating**.
- Final trigger persistence: `save_alert_if_new(symbol, breakout_type="INTRADAY", scanner="multi_tf_scanner", ...)` + `verify` is **NOT** called (see MED-MTF-01).
- S/L: `compute_sl_and_target(..., mode="INTRADAY")`.

Minimum internal data expectations:
- Phase A requires `len(df) >= 200` for 1h (`multi_tf_scanner.py:116`) and `len(df) >= 2` after `strip_forming_candle`.
- Required 1h indicator columns: `EMA9, EMA20, SMA50, SMA200, ADX, PRIOR_20D_HIGH` (`:132`).
- Phase D 5m requires `EMA9, ATR20, Volume` (`:513`).

---

## Walkthrough: Data Path & Key Checks

1. **Scheduler loop** (`_start_wrapper`, `:696`): runs forever; every 5 min when market open (`is_market_open` or `run_once`). On crash → `upsert_scanner_health("MULTI_TF","DOWN",...)` then `sleep(60)`.
2. **Sweeper** (`:739`): `sweep_stale_breakout_watchlist()` demotes/expires stale states every cycle.
3. **Phase A — 1h trend permission** (`:68`): fetch 1h, abort if `<70%` fetched (raises → whole cycle aborts, catches in wrapper). For each symbol: `len<200` skip, stale skip, `strip_forming_candle(60)`, `apply_indicators(1h)`, require cols, price≥MIN_STOCK_PRICE, NaN→hard skip, `dist_to_breakout = (PRIOR_20D_HIGH - close)/PRIOR_20D_HIGH`, gate `ema_ok (9>20>50 & close>200)` AND `adx>20` AND `0<=dist<=0.05` → `upsert_breakout_watchlist(HOURLY_APPROVED)`.
4. **Phase B/C/D** (`:234`): pull `get_active_breakout_watchlist()`, bucket symbols by needed TF, fetch each TF, advance state per gate:
   - Phase B (30m): `bb_pctile<0.45` AND `dist in [-0.015,0.025]` (consolidation) OR fast-breakout → `SETUP_ARMED`.
   - Phase C (15m): `EMA9>EMA20` AND `dist in [-0.015,0.025]` → `ENTRY_READY`.
   - Phase D (5m): thrust (`close>prev High` & `close>trigger+buffer` & `vol>1.2` & body/upper-wick quality) OR pullback trigger → `check_recent_alert` → `compute_sl_and_target(INTRADAY)` → `rr_ratio>=1.5` → `save_alert_if_new(INTRADAY)` + `TRADE_ACTIVE` + `cooldown 24h`.
5. **Health roll-up** (`:752`): `DEGRADED` if stale/total>10% or partial fetch. Otherwise `OK`/`IDLE`.

---

## Detailed Issues (Severity, Location, Explanation, Impact, Detection)

### Critical

1. **CRIT-MTF-01: Intraday data dependency → silent zero alerts (`:87`, `:116`, `data_provider.py:229-345`)**
   - Explanation: Phase A requires **200 hourly bars**. The provider is `AutoSwitchingFetcher`: Fyers if authenticated, else YFinance. Fyers gives real intraday NSE candles (works). But if Fyers auth is missing/expired, it falls back to **YFinance intraday**, which for `.NS` symbols is frequently shallow, delayed, or returns far fewer than 200 bars (YFinance 1h/30m/15m/5m for Indian stocks is historically unreliable / often limited to 7 days). When `len(df) < 200`, every symbol is skipped at `:116` → **zero `HOURLY_APPROVED`** → ladder never starts → **zero alerts**, while `scanner_health` is just `OK`/`IDLE`. No DOWN, no operator alarm.
   - Impact: Entire intraday scanner produces nothing for the day with a green health light. Highest-severity silent failure.
   - Detection: Alert if `(metrics_a.get("approved",0) == 0)` on a trading day when the market was open; alarm if `fetched_count >= required_count` but `funnel["data_ok"]==0`; monitor Fyers token validity (`fyers_ping.lock`).

2. **CRIT-MTF-02: Phase A `<70%` abort kills the whole cycle (`:97-99`)**
   - Explanation: If the 1h batch fetch returns `<70%`, `run_hourly_phase` raises; the wrapper catches it, sets `DOWN`, sleeps 60s. The raise **aborts the entire cycle**, so Phase B/C/D never run that pass — even if 30m/15m/5m data for already-active symbols was available.
   - Hidden scenario: One transient 1h hiccup aborts intraday ladder maintenance for that 5-min window; if it recurs every cycle, active setups drift and expire without updates.
   - Impact: Missed/late ladder advancement during rate-limit storms.
   - Detection: Count `MULTI_TF DOWN` events and correlate with 1h fetch counts.

3. **CRIT-MTF-03: Forming-candle strip on a provider that returns the latest bar as "completed"**
   - Explanation: `strip_forming_candle(df, tf, ist_now)` removes the last bar if `now_naive < candle_end` (`:25-63`). It relies on the data provider's last bar timestamp being the *true* last completed bar. If the provider (esp. YFinance fallback) returns the in-progress candle as the last row with a timestamp already at/past `candle_end` (common with cached/rounded intraday), the strip will **not** remove it and the scanner may trigger on a still-forming 5m/30m bar.
   - Impact: False intraday trigger on incomplete candle, then the next candle invalidates it (no SL re-check). Quality damage.
   - Detection: Log `df.index[-1]` vs `ist_now` for the final bar on every Phase D trigger; alert if trigger fired within the open window of the current candle.

### High

4. **HIGH-MTF-01: `_check_fetch()` return value ignored (`:280-282`)**
   - Explanation: `run_lower_tf_phase` calls `_check_fetch(data_30m, needs_30m, "30m")` etc. but **discards the boolean**. So even if a TF batch fails the 70% threshold, the phase continues. Graceful degradation (per-symbol `data_tf.get(symbol) is None` → skip) prevents a crash, but the function is effectively dead code and gives a false sense of gating.
   - Impact: No crash, but misleading; a half-fetched TF silently produces no upgrades for that segment.
   - Detection: Log when `_check_fetch` would have returned False (currently only `logger.error` inside, which IS emitted — so at least visible in logs).

5. **HIGH-MTF-02: Only near-the-level names are ever approved (`:189`)**
   - Explanation: `dist_ok = 0.0 <= dist_to_breakout <= 0.05`. A stock already >5% above its 20d high (a *strong* live breakout) has `dist_to_breakout < 0`? No — if close > prior_high, `dist_to_breakout` is negative, which **is** within `[0.0, 0.05]`? Negative is NOT `>= 0.0`, so it is **excluded**. So once a stock breaks out and runs >0% above the level it is *never* re-approved (each cycle `dist<0` → fails `dist_ok`). The scanner only "watches" names within 0–5% of the level.
   - Impact: By design it catches the breakout attempt, but it will never re-arm a stock that has already broken out and is trending — the ladder only fires on the approach/initial breakout. Acceptable by design, but operators should know MULTI_TF will not re-alert an already-running breakout.
   - Detection: Document; sample `dist_to_breakout` distribution for approved vs skipped.

6. **HIGH-MTF-03: VWAP/SL depends on intraday Volume presence (`:226`, `compute_sl_and_target`)**
   - Explanation: `apply_indicators` only computes `VWAP` if `"Volume" in df.columns`; the INTRADAY SL helper uses `vwap`. If a provider returns intraday bars without Volume (rare but possible on fallback), `VWAP` is NaN and the SL anchor degrades to fallbacks inside `compute_sl_and_target` → possibly low R:R → `low_rr` suppression.
   - Impact: Valid setups suppressed on low-quality SL; or SL placed on a weak anchor.
   - Detection: Track `low_rr` from Phase D and inspect `VWAP` presence for triggered symbols.

7. **HIGH-MTF-04: `save_alert_if_new` is the only persistence guard; no `verify_alerts_saved_today` (`:626`, contrast `:855`)**
   - Explanation: Unlike REVERSAL/EOD, MULTI_TF does **not** call `verify_alerts_saved_today()`. It trusts `inserted` from `save_alert_if_new`. If the DB write silently fails (returns `inserted=False` with a conflict/exception reason), the symbol keeps `current_state=ENTRY_READY` and **retries next cycle** — resilient — but there is **no `DOWN` health flip** for an alert-save failure, so a systemic DB outage during market hours shows `OK` while alerts are dropped.
   - Impact: Silent missed intraday alerts under DB stress.
   - Detection: Add a counter of `(inserted==False and reason!="DB CONFLICT")` and flip health to DEGRADED/DOWN when >0 in a cycle.

8. **HIGH-MTF-05: State machine race between sweeper and re-approval (`:739`, `:206`)**
   - Explanation: `sweep_stale_breakout_watchlist()` marks expired `HOURLY_APPROVED/SETUP_ARMED/ENTRY_READY` as `FAILED`. But Phase A re-approves passing stocks every 5 min and resets `expires_at` to end-of-session. A stock that drifts >3% from its level is demoted in Phase B (`:354`), yet Phase A may re-approve it next cycle (dist still in 0–5% of the *new* 20d high which also drifted). Net effect: oscillation HOURLY_APPROVED ↔ demoted during choppy days.
   - Impact: Flapping watchlist state; possible repeated re-entry after a failed approach.
   - Detection: Track churn = count of symbols whose `current_state` changed more than once per day.

9. **HIGH-MTF-06: `apply_indicators` called without `daily_ohlc` → pivot S/R from intraday prior bar (`:127`, `:385`, `:453`, `:512`)**
   - Explanation: `apply_indicators(df, timeframe="5m")` is called with default `daily_ohlc=None`. The docstring says pivot points (PP/S1–S3/R1–R3) should use *previous day's* OHLC for intraday; without it, they're computed from the previous *intraday* bar — meaningless S/R. `compute_sl_and_target` consumes S1/S2/R1/R2. This makes pivot-based SL/target placement weaker for MULTI_TF than for EOD.
   - Impact: Sub-optimal SL/target on intraday alerts (uses intrabar pivots instead of daily pivots).
   - Detection: Compare `S1/R1` values produced for a 5m frame vs daily-pivot expectation; low quality scores on SL rationale.

### Medium / Operational

10. **MED-MTF-01:** See HIGH-MTF-04 — missing verify step (operational gap, not crash).
11. **MED-MTF-02: `get_active_breakout_watchlist` excludes `TRADE_ACTIVE` (`:3716`)** — after a trigger, the symbol disappears from the active set (correct), but it is *also* excluded from the sweeper's demotion (sweeper only touches the 3 non-terminal states). So a `TRADE_ACTIVE` row persists with its `expires_at`; fine, but verify a separate cleanup removes stale `TRADE_ACTIVE` rows (otherwise the table grows). Check whether any job purges `TRADE_ACTIVE` after the 24h cooldown.
12. **MED-MTF-03: HOURLY_APPROVED `expires_at = end_of_session` reset every cycle (`:214`)** — means HOURLY_APPROVED effectively never expires intraday (re-upped each pass). Only demotion (drift) or end-of-session sweep removes it. Acceptable but means the ladder can hold dead approvals all day.
13. **MED-MTF-04: Shared global fetch lock with nightly scanners (`price_cache._fetch_lock`)** — if an operator manually runs MULTI_TF at night (`run_once=True`) while REVERSAL/EOD are running, both serialize on the global lock; MULTI_TF intraday fetch may block behind a 2y EOD fetch. Usually no overlap (schedule), but manual triggers can collide.
14. **MED-MTF-05: `funnel`/`lower_funnel` logging is good**, but per-symbol rejection *reasons* for Phase D are only `logger.info` ("SUPPRESSED: low R:R" / reason) — no persistent counter dict. Hard to trend why triggers were suppressed.
15. **MED-MTF-06: Thread/process safety** — `_scan_lock = ProcessLock("multi_tf_scanner")` prevents two instances; `start(run_once=False)` busy-waits 60s for the lock. If a prior crash leaves the lock file, the scanner won't start. Check lock-file cleanup on the Railway container.

---

## Combination Failure Scenarios (Hidden Bugs)

A. **Fyers token expires mid-day + YFinance intraday shallow**
- Cause: `AutoSwitchingFetcher._should_use_fyers()` returns False (expired token) → YFinance fallback. YFinance returns <200 1h bars for many `.NS`.
- Effect: `len(df) < 200` at `:116` skips all → `approved=0` → zero alerts, health OK.
- Detection: `approved==0` on open trading day + Fyers ping lock date != today.

B. **Rate-limit cascade across scanners**
- Cause: `yf_rate_limiter` circuit opens (shared). MULTI_TF 1h batch hits it → `<70%` → CRIT-MTF-02 abort (DOWN). Meanwhile if nightly EOD/reversal also run against the same limiter, they abort too.
- Effect: Whole-system intraday + nightly outage under one rate-limit event.
- Detection: Central `record_rate_limit` count + `MULTI_TF DOWN` correlation.

C. **Timezone off-by-one on forming candle**
- Cause: Provider returns UTC-aware last bar; `strip_forming_candle` does `tz_convert(IST)` (correct) — OK. BUT if provider returns *naive* timestamps that are actually UTC (not IST), the code does `tz_localize(IST)` (`:52`), adding 5.5h, so `candle_end` is computed 5.5h late → the forming bar is **not** stripped → trigger evaluated on incomplete candle.
- Effect: False 5m thrust trigger at the open of a new candle; reversed next bar.
- Detection: Compare provider last-bar tz (naive vs aware) and `close` vs real-time quote at trigger time.

D. **DB write silently fails during volatility spike**
- Cause: At 15:00–15:25 the most triggers fire; DB under load → `save_alert_if_new` returns `inserted=False`. MULTI_TF does no `verify_alerts_saved_today` (HIGH-MTF-04) → no DOWN.
- Effect: Best setups of the day dropped with green health.
- Detection: Add inserted-failure counter → DEGRADED.

E. **Choppy-day flapping (HIGH-MTF-05)**
- Cause: drift demotion in Phase B vs re-approval in Phase A on a ranging stock.
- Effect: Repeated HOURLY_APPROVED→demoted→approved; possible multiple ENTRY_READY re-arms (guarded by 24h cooldown only after a *successful* trigger, so re-arms before any trigger are allowed).
- Detection: State-change count per symbol per day.

---

## Cross-Scanner Notes (verified against the other 3 scanners)

- **Dedup key mismatch (present in REVERSAL `:587` and EOD `:569`, ABSENT here):** MULTI_TF correctly uses `check_recent_alert(symbol, scanner, breakout_type, 390)` against the DB. The other two scanners build `dedup_key = f"{category}|{symbol}|{today}|{type}"` and test `(symbol, dedup_key) in cooldown_alerts`, but `get_recent_alerts_for_scanner()` returns a `set[(symbol, breakout_type)]` (`database.py:1297`). The second element never matches → that in-memory dedup is **dead code** in REVERSAL and EOD (the real DB `ON CONFLICT (symbol, breakout_type, scanner, alert_date)` still prevents true duplicates). MULTI_TF avoids this bug.
- **DB-level stale guard is dead code for all 4 scanners:** `save_alert_if_new._is_stale_buy()` (`database.py:1134`) only suppresses when the caller passes `used_fallback_data` / `data_quality` in `context`/`kwargs`. None of the four scanners populate those flags, so the guard never fires. Stale-alert protection relies entirely on each scanner's own `df.attrs['is_stale']` checks (which MULTI_TF does at `:574`, `:338`, `:374`, `:442`, `:501`).
- **Shared global fetch lock & rate limiter:** all scanners share `price_cache._fetch_lock` and `yf_rate_limiter`. Contention is scheduled apart (MULTI_TF intraday; others nightly) but manual runs can collide.

---

## Observability & Operational Recommendations (No code changes)

1. **Alert on `approved == 0`** during any open trading day (catches CRIT-MTF-01/02 silently).
2. **Monitor Fyers token health** (`fyers_ping.lock` date) and alert on fallback-to-YFinance for intraday.
3. **Add an inserted-failure counter** in Phase D and flip health DEGRADED/DOWN when >0 (closes HIGH-MTF-04).
4. **Log provider last-bar tz + timestamp** on each Phase D trigger to detect forming-candle misuse (CRIT-MTF-03 / combo C).
5. **Track intraday bar counts** (`len(df)`) per TF per symbol; alarm if the 200-bar minimum is systematically unmet (data-source regression).
6. **Add `verify_alerts_saved_today("multi_tf_scanner", total_alerts)`** after the loop, mirroring REVERSAL/EOD, so DB outages during market hours are surfaced.
7. **Churn metric** for `breakout_watchlist` state changes per symbol per day (combo E).

---

## Appendix: Key Line References

- Scheduler loop: `:696` (`_start_wrapper`)
- Phase A fetch + 70% abort: `:87`–`:99`
- Phase A bar/indicator gates: `:116`–`:218`
- `strip_forming_candle`: `:25`–`:63`
- Phase B/C/D: `:234`–`:666`
- `_check_fetch` (return ignored): `:271`–`:282`
- Phase D trigger + persist: `:495`–`:650`
- Health roll-up: `:752`–`:781`
- DB state store: `database.py:3615` (`upsert_breakout_watchlist`), `:3707` (`get_active_breakout_watchlist`), `:3744` (`sweep_stale_breakout_watchlist`), `:3727` (`mark_breakout_watchlist_cooldown`)

---

End of `MULTI_TF` (mf) scanner full review.
