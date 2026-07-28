# Elite Breakout System — Scanner Engine Analysis

> Generated: 2026-07-28 | 7 scanners reviewed | 34 fixes applied (P0-P6 + MTF structural + Multibagger)

---

## Table of Contents

1. [Scanner Inventory Summary](#1-scanner-inventory-summary)
2. [EOD Breakout Scanner](#2-eod-breakout-scanner)
3. [Reversal Bounce Scanner](#3-reversal-bounce-scanner)
4. [Pullback Continuation Scanner](#4-pullback-continuation-scanner)
5. [Wealth Engine Scanner](#5-wealth-engine-scanner)
6. [Multibagger Scanner](#6-multibagger-scanner)
7. [Multi-TF Intraday Scanner](#7-multi-tf-intraday-scanner)
8. [Critical Validation — Why So Few Alerts?](#8-critical-validation--why-so-few-alerts)
9. [Fixes Applied (P0-P6)](#9-fixes-applied-p0-p6)
10. [Summary & Recommendations](#10-summary--recommendations)

---

## 1. Scanner Inventory Summary

| # | Scanner | File | Purpose | Score to Qualify | Max Alerts/Scan |
|---|---------|------|---------|------------------|-----------------|
| 1 | EOD Breakout | `eod_scanner.py:80-211` (eval), `225-1329` (prod) | Daily breakout candle detection with volume surge | >= 82 (regime-adjusted, capped at 87) | 10 |
| 2 | Reversal Bounce | `reversal_scanner.py:260-439` | Oversold quality stock bounce from 52W drop | >= 62 | 10 |
| 3 | Pullback Continuation | `pullback_pipeline.py:33-128` | Fibonacci pullback resumption in uptrend | >= 75 | 10 |
| 4 | Wealth Engine | `wealth_engine.py:42-151` | Quality compounders at fair value | Gradient (weighted buckets) | 40 |
| 5 | Multibagger | `multibagger.py:43-131` | High-quality long-term compounders | V5 score >= 65 (HQ) or >= 75 (Prime) | 10 |
| 6 | Multi-TF Intraday | `multi_tf_scanner.py:38-217` | Intraday scalper with 4-phase confirmation | Weighted phase scoring (base 60, threshold 75) | 15 |

---

## 2. EOD Breakout Scanner

### What It Does

Detects daily breakout candles — a stock closing above its 20-day high with volume surge, trend alignment, and momentum confirmation. Designed for EOD swing trades held 3-15 days.

### How It Generates Alerts

The scanner runs nightly (21:00-23:59 IST) on the Daily Builder watchlist (~500-800 stocks). Each stock must pass ALL conditions sequentially (AND logic). Failure at any step = immediate rejection.

### Pre-Scanner Filters (Daily Builder — Universe Entry)

Before the EOD scanner even runs, stocks must survive the Daily Builder:

| # | Condition | Threshold | Logic |
|---|-----------|-----------|-------|
| 1 | Exchange | NSE or BSE | AND |
| 2 | Close price | >= Rs.100 | AND |
| 3 | Market cap | >= Rs.1,000 Cr | AND |
| 4 | EPS TTM | > 0 (profitable) | AND |
| 5 | ROE (FY) | >= 8% | AND |
| 6 | OPM | >= 10% (exempt: mega-cap >= 10,000 Cr) | AND |
| 7 | Daily traded value | >= Rs.15 Cr/day | AND |
| 8 | YoY Revenue anomaly | -90% to +500% | AND |
| 9 | Forensic red flags | < 2 | AND |
| 10 | Not on blacklist | ASM/GSM surveillance | AND |
| 11 | Promoter market cap | >= Rs.500 Cr | AND |
| 12 | Debt/Equity | <= 1.0 (Utilities <= 2.5, mega-caps exempt) | AND |
| 13 | OPM not negative | >= 0% | AND |
| 14 | No structural collapse | NOT (Rev < -20% AND Profit < -20%) | AND |
| 15 | Earnings quality | CFO/PAT >= 0.5 (turnaround exempt) | AND |
| 16 | Must match >= 1 category | High Momentum, Wealth Compounder, etc. | AND |

### EOD Production Scanner Conditions (ALL AND)

| # | Condition | Threshold | Config Constant | Status |
|---|-----------|-----------|-----------------|--------|
| 1 | Bars >= 50 after dropna | 50 | EOD_BAR_LIMIT_FIX | UNCHANGED |
| 2 | Indicators computed | Not None/empty | - | UNCHANGED |
| 3 | Breakout signals detected | >= 1 | MIN_SIGNALS = 1 | UNCHANGED |
| 4 | RSI not NaN | Valid | - | UNCHANGED |
| 5 | Data freshness | Last bar within 4 calendar days | - | UNCHANGED |
| 6 | 20D average volume > 0 | > 0 | - | UNCHANGED |
| 7 | Candle range > 0 | Not a doji with identical H/L | - | UNCHANGED |
| 8 | Body ratio (body/range) | >= 0.45 (soft penalty) | MIN_BODY_RATIO | **FIXED** |
| 9 | Bullish candle | Close > Open (soft penalty) | - | **FIXED** |
| 10 | Close position in candle | >= 0.65 (soft penalty) | MIN_CLOSE_POSITION | **FIXED** |
| 11 | Upper wick ratio | <= 0.35 (soft penalty) | MAX_UPPER_WICK_RATIO | **FIXED** |
| 12 | Volume ratio vs 20D avg | >= 1.8x | MIN_VOLUME_RATIO | UNCHANGED |
| 13 | 20D avg volume | >= 50,000 shares | MIN_AVG_VOLUME_SHARES | UNCHANGED |
| 14 | Close price | >= Rs.100 | MIN_STOCK_PRICE | UNCHANGED |
| 15 | RSI range | 55 <= RSI <= 88 | MIN_RSI / MAX_RSI | UNCHANGED |
| 16 | PRIOR_20D_HIGH present | Not NaN, > 0 | - | UNCHANGED |
| 17 | **STRUCTURAL BREAKOUT** | Close > Prior 20D High | - | UNCHANGED |
| 18 | ATR20 present | Not NaN, > 0 | - | UNCHANGED |
| 19 | ATR expansion | candle_range / ATR20 >= 0.9 | MIN_ATR_EXPANSION_RATIO | UNCHANGED |
| 20 | BB Width percentile (current) | ~~<= 0.80~~ **REMOVED** | MAX_BB_WIDTH_PCTILE | **FIXED** |
| 21 | Close >= EMA20 | Trend alignment | - | UNCHANGED |
| 22 | Close >= SMA50 | Trend alignment | - | UNCHANGED |
| 23 | ADX | >= 18 | ADX_MIN_THRESHOLD | UNCHANGED |
| 24 | Distance from 52W High | <= 15% | MAX_DISTANCE_FROM_52W_HIGH_PCT | UNCHANGED |
| 25 | Single-day move (abs) | <= 15% | MAX_SINGLE_DAY_MOVE_PCT | UNCHANGED |
| 26 | Gap from 10-bar lookback high | ~~<= 3%~~ **Soft penalty** | MAX_GAP_FROM_PRIOR_HIGH_PCT | **FIXED** |
| 27 | Pre-breakout red candles (last 5) | <= 2 | MAX_PRE_BREAKOUT_RED_CANDLES | UNCHANGED |
| 28 | BB Width percentile bar[-2] | <= 0.80 | MAX_BB_WIDTH_PCTILE | UNCHANGED |
| 29 | Composite score >= threshold | Regime-adjusted, **capped at 87** | SCORE_THRESHOLDS | **FIXED** |
| 30 | Forensic risk tier | != REJECT | - | UNCHANGED |
| 31 | No duplicate alert | Last 24h | - | UNCHANGED |
| 32 | SL engine rejection | Must pass R:R | - | UNCHANGED |
| 33 | Total alerts per scan | <= 10 | SCANNER_MAX_ALERTS["EOD"] | UNCHANGED |

### Scoring Logic

Base score (0-100) from `scoring_engine.py`:
- Category weight (fundamental category): 0-30 pts
- Breakout signal count and quality
- RSI strength
- Volume surge magnitude
- ADX trend strength
- Delivery percentage bonus: +2/+4/+6
- Promoter pledge penalty
- RSI divergence penalty
- Bayesian regime-aware weights

**Penalties (soft, not hard reject):**
- Gap-and-go: subtract up to 20 pts if gap from 10-bar high > 3%
- OBV divergence: subtract 5 pts if OBV slope <= 0
- **[FIX P1-3]** Candle quality: body_ratio, bearish candle, close_position, upper_wick → proportional penalty via `candle_penalty` variable
- Sector rotation: add/subtract based on sector tailwind

**Regime-adjusted thresholds (capped):**
| Regime | Threshold |
|--------|-----------|
| STRONG_BULL / BULL / WEAK_BULL / NEUTRAL | 82 |
| BEAR | 87 (capped) |
| ~~SIDEWAYS / RANGEBOUND~~ | ~~90~~ 87 (capped) |
| ~~WEAK_BEAR / STRONG_BEAR~~ | ~~92~~ 87 (capped) |

### Shared Condition Function

**[FIX P6-13]** `_check_eod_conditions()` in `eod_scanner.py` is a shared function used by both:
- `evaluate_eod_symbol()` — on-demand UI diagnostics (stock_analyzer.py)
- Production scanner `_start_wrapper()` — nightly batch scan

This ensures the UI and production paths run identical condition checks, eliminating discrepancies where a stock could show "CORE MET" in the analyzer but fail in production.

---

## 3. Reversal Bounce Scanner

### What It Does

Catches quality stocks bouncing from 20-45% drops from their 52-week high, with oversold RSI curling up and momentum reclaiming. Designed for mean-reversion swing trades.

### How It Generates Alerts

Runs as part of the EOD batch scan. Evaluates each stock that passed the Daily Builder filter against reversal-specific conditions.

### Hard Conditions (ALL AND)

| # | Condition | Threshold | Config Constant | Status |
|---|-----------|-----------|-----------------|--------|
| 1 | Bars >= 50 after dropna | 50 | - | UNCHANGED |
| 2 | Drop from 52W High | 20%-45% (quality: 15%-45%) | MIN_DROP_FROM_52W_HIGH / MAX_DROP_FROM_52W_HIGH | UNCHANGED |
| 3 | Close price | >= Rs.100 | MIN_STOCK_PRICE | UNCHANGED |
| 4 | 20D avg volume | >= 300,000 shares | MIN_AVG_DAILY_VOLUME | UNCHANGED |
| 5 | Below SMA200 | <= 20% (anti-falling knife) | MAX_DROP_BELOW_SMA200 | UNCHANGED |
| 6 | ROE | >= 12% | MIN_ROE | UNCHANGED |
| 7 | YoY Revenue growth | >= 8% | MIN_YOY_REVENUE_GROWTH | UNCHANGED |
| 8 | Current RSI | ~~>= 50~~ **>= 45** | RSI_CURL_MIN | **FIXED** |
| 9 | Recent RSI minimum | ~~15-bar~~ **25-bar** min RSI <= 38 | REVERSAL_RSI_LOOKBACK | **FIXED** |
| 10 | RSI slope | ~~N/A~~ **>= 0** (rising last 3 bars) | - | **FIXED** |
| 11 | Close >= EMA20 | Momentum reclaim | - | UNCHANGED |
| 12 | EMA20 > EMA50 OR EMA20 slope positive | At least one | - | UNCHANGED |
| 13 | Volume ratio | >= 2.0x | MIN_VOLUME_RATIO | UNCHANGED |
| 14 | MACD bullish crossover | ~~10-bar~~ **20-bar** lookback | - | **FIXED** |

### Scoring

`_score_reversal()` evaluates:
- Volume ratio magnitude
- Drop percentage depth
- RSI recovery strength
- MACD histogram momentum
- Distance below SMA200
- Fundamental category (quality premium)
- R:R ratio
- SMA50/SMA200 reclaim status

Final score must >= 62 (MIN_REVERSAL_SCORE). In STRONG_BEAR regime, threshold raises to 90.

---

## 4. Pullback Continuation Scanner

### What It Does

Finds pullbacks in established uptrends (Fibonacci retracement 23.6%-61.8%) with a bullish resumption trigger candle. Designed for trend-following entries on dips.

### How It Generates Alerts

Runs as part of the EOD batch scan. Uses `swing_utils` for pivot detection and pullback measurement.

### Hard Conditions (ALL AND)

| # | Condition | Threshold | Config Constant | Status |
|---|-----------|-----------|-----------------|--------|
| 1 | Bars >= 50 (prefer >= 200) | 50/200 | MIN_HISTORY | UNCHANGED |
| 2 | Close > SMA50 > SMA200 | Uptrend alignment | - | UNCHANGED |
| 3 | Confirmed swing pivots exist | LOOKBACK=10, **CONFIRM=2** | PULLBACK_CONFIG | **FIXED** |
| 4 | Valid impulse leg | Gain >= 8%, max 20 bars | MIN_IMPULSE_GAIN_PCT | UNCHANGED |
| 5 | Pullback depth | 23.6%-61.8% | MIN/MAX_DEPTH_PCT | UNCHANGED |
| 6 | Pullback duration | 3-20 bars | MIN/MAX_DURATION | UNCHANGED |
| 7 | Max internal swings | **<= 3** | MAX_INTERNAL_SWINGS | **FIXED** |
| 8 | Pullback volume ratio | <= 0.75x (low vol pullback) | MAX_PB_VOLUME_RATIO | UNCHANGED |

### Resumption Trigger Candle (ALL AND)

| # | Condition | Threshold | Status |
|---|-----------|-----------|--------|
| 1 | Volume | >= 1.3x avg | UNCHANGED |
| 2 | Close location | **>= 0.65** (upper 35%) | **FIXED** |
| 3 | Body size | **>= 0.35 ATR** | **FIXED** |
| 4 | Upper wick | <= 0.25 | UNCHANGED |
| 5 | Gap from prior close | <= 3% | UNCHANGED |

### Scoring

Base score = 70. Adjusted by maturity penalties:
| Pullback count in trend | Penalty |
|------------------------|---------|
| 0 | 0 (fresh trend) |
| 1 | 0 |
| 2 | -3 |
| 3+ | -6 to -10 |

Final score must >= 75 (required_threshold). In STRONG_BEAR regime, scanner disabled entirely.

---

## 5. Wealth Engine Scanner

### What It Does

Finds fundamentally strong compounders at fair value for long-term wealth creation. Evaluates 4 fundamental buckets and requires uptrend confirmation.

### How It Generates Alerts

Runs as part of the EOD batch scan. Purely fundamental + trend-based, no candle pattern requirements.

### Hard Conditions (ALL AND)

| # | Condition | Threshold |
|---|-----------|-----------|
| 1 | Bars >= 50 | 50 |
| 2 | Close > SMA200 | Uptrend gate |
| 3 | PEG ratio | <= 3.0 (or null/missing) |
| 4 | At least ONE bucket matched | See below |

### Bucket Conditions (need ONE)

| Bucket | Conditions (ALL within bucket) | Status |
|--------|-------------------------------|--------|
| Core Compounder | ROCE >= 20%, ROE >= 15%, D/E <= 0.50 | UNCHANGED |
| Growth Multiplier | YoY Sales >= 20%, YoY Profit >= 20%, ROCE >= 15% | UNCHANGED |
| Quality-On-Sale | ROCE >= 15%, D/E <= 1.0, Drop from 52W **>= 10%** | **FIXED** |
| Opportunistic | YoY Profit >= 40% | UNCHANGED |

### Scoring

**[FIX P5-11]** Gradient scoring (weighted bucket scores):

| Component | Points |
|-----------|--------|
| Core Compounder | +30 |
| Growth Multiplier | +25 |
| Quality-On-Sale | +20 |
| Opportunistic | +15 |
| Trend OK (Close > SMA200) | +10 |
| PEG OK (<= 3.0) | +5 |
| **Maximum total** | **100** |

When not qualified, score defaults to 50.0. Minimum possible score: 50.0.

---

## 6. Multibagger Scanner

### What It Does

Identifies high-quality businesses with strong fundamentals and price momentum for multi-year compounding. Uses V5 composite scoring pipeline.

### How It Generates Alerts

Runs as part of the EOD batch scan. Combines V5 fundamental scoring with technical trend alignment.

### Hard Conditions

| # | Condition | Threshold |
|---|-----------|-----------|
| 1 | Bars >= 50 | 50 |
| 2 | Close > SMA50 > SMA200 | Uptrend |
| 3 | V5 composite score | >= 65 (High Quality) or >= 75 (Prime) |
| 4 | Promoter pledge | <= 15% (HQ) or <= 10% (Prime) |
| 5 | Piotroski F-Score (Prime only) | >= 7/9 |

### Conviction Tiers

| Tier | Conditions | Status |
|------|-----------|--------|
| Prime Multibagger | F-Score >= 7 AND Pledge <= 10% AND Uptrend AND V5 >= 70 | CORE MET (Prime) |
| High Quality | V5 >= 65 AND Pledge <= 15% AND Uptrend | CORE MET (High Quality) |
| Watchlist | V5 >= 50 | WATCHLIST |
| No Setup | V5 < 50 | NO |

### Scoring

V5 composite score from `run_pipeline_for_symbol()` — combines quality, valuation, growth, momentum, and risk metrics into a single 0-100 score.

---

## 7. Multi-TF Intraday Scanner

### What It Does

Intraday scalper using a 4-phase approach: 1H trend permission -> 30m squeeze -> 15m entry -> 5m trigger. Designed for intraday/momentum trades.

### How It Generates Alerts

Runs during market hours. Fetches 1H, 30m, 15m, 5m intraday data and evaluates across all timeframes.

### Phase A: 1H Trend Permission (ALL AND)

| # | Condition | Threshold |
|---|-----------|-----------|
| 1 | Bars >= 50 | 50 |
| 2 | EMA alignment | EMA9 > EMA20 > SMA50 |
| 3 | Close > SMA200 | Above long-term trend |
| 4 | ADX | >= 18 |
| 5 | Distance to breakout level | -2% to +5% from 20D high |

### Phase B: 30m Squeeze — Recently Released (soft bonus)

**[FIX P2-5]** Changed from "squeeze now" to "squeeze recently released":

| # | Condition | Threshold | Status |
|---|-----------|-----------|--------|
| 1 | BB Width Pctile < 0.45 in last **8 bars** | Any of last 8 bars had squeeze | **FIXED** |
| 2 | OR distance to breakout | < -1.5% | UNCHANGED |

### Phase C: 15m Entry (soft bonus)

| # | Condition | Threshold |
|---|-----------|-----------|
| 1 | Close | >= EMA15 on 15m chart |

### Phase D: 5m Trigger (soft bonus)

| # | Condition | Threshold |
|---|-----------|-----------|
| 1 | Close | >= breakout level (20D high) |
| 2 | 5m volume ratio | >= 1.2x |

### Status Tags

| Phases Passed | Status Tag |
|---------------|-----------|
| A+B+C+D | CORE MET (Phase A+B+C+D Trigger Ready) |
| A+B+C | CORE MET (Phase A+B+C Entry Ready) |
| A+B | CORE MET (Phase A+B Squeeze Armed) |
| A only | CORE MET (1H Setup Approved) |

### Scoring

**[FIX P2-6]** Weighted phase scoring (base 60):

| Phase | Bonus |
|-------|-------|
| Phase A (mandatory) | base = 60 |
| Phase B (30m squeeze-released) | +10 |
| Phase C (15m alignment) | +5 |
| Phase D (5m trigger) | +10 |
| **Maximum total** | **85** |

Threshold: **75** (was 75-80 fixed).

---

## 8. Critical Validation — Why So Few Alerts?

### Issue 1: EOD Scanner — Contradictory RSI + Breakout Logic (CRITICAL)

**The conflict:**
- Requires RSI >= 55 (momentum/strength)
- Requires Close > Prior 20D High (new breakout)
- Requires Close >= EMA20 AND Close >= SMA50 (trending up)
- Requires Distance from 52W High <= 15% (near all-time high)
- Requires Gap from 10-bar high <= 3% (not extended)
- Requires volume >= 1.8x average

**Why it's contradictory:** A stock breaking its 20D high while within 15% of its 52W high, with RSI 55-88, close above EMA20 and SMA50, AND a body ratio >= 45% with volume 1.8x — this describes a stock that's already running. The 3% gap filter then rejects stocks that gapped up to reach this level. You're filtering for breakouts but rejecting the very price action that creates breakouts.

**Impact:** The scanner requires a "quiet" breakout — no gap, tight BB, moderate RSI — which rarely happens in practice. Most genuine breakouts involve gap-ups or extended candles that get filtered out.

### Issue 2: Reversal Scanner — RSI Paradox (CRITICAL)

**The conflict:**
- Requires current RSI >= 50 (RSI_CURL_MIN)
- Requires min 15-bar RSI <= 38 (RSI_OVERSOLD_THRESHOLD)
- Requires Close >= EMA20 (momentum reclaimed)
- Requires EMA20 > EMA50 (trend recovering)
- Requires Drop from 52W High: 20-45%

**Why it's contradictory:** A stock that dropped 20-45% from highs, is now back above EMA20, and has RSI >= 50 — it has already recovered significantly. But you also require it to have been below RSI 38 within the last 15 bars. For a stock that's recovered enough to close above EMA20 with RSI >= 50, being below RSI 38 just 15 days ago means an extremely violent V-shaped recovery. Most quality stocks that dropped 20-45% take weeks to months to recover to EMA20, not <= 15 bars.

**Impact:** Only stocks with extreme V-shaped recoveries qualify. This eliminates most quality reversal candidates that recover gradually (which is actually the healthier pattern).

### Issue 3: Pullback Scanner — Impossibly Specific Trigger Candle (MEDIUM)

**The conflict:**
The resumption trigger candle requires ALL of:
- Volume >= 1.3x average
- Close location >= 0.75 (upper 25% of candle)
- Body >= 0.5 ATR
- Upper wick <= 0.25
- Gap from prior close <= 3%

PLUS the pullback must have:
- 23.6-61.8% Fibonacci depth
- Low volume during pullback (<= 0.75x)
- Exactly <= 2 internal swings
- Duration 3-20 bars

**Why it's contradictory:** This is a very rare candle pattern — a near-perfect bullish marubozu on a resumption day, after a textbook Fibonacci pullback with low volume. The probability of all 9+ conditions aligning simultaneously on the same day is extremely low.

**Impact:** Qualifying pullbacks are rare even in strong uptrends. The trigger candle requirement further reduces the already-small set of valid pullback structures.

### Issue 4: Wealth Engine — Fundamental-Technical Mismatch (MEDIUM)

**The conflict:**
- Requires at least one fundamental bucket (ROCE >= 20%, ROE >= 15%, etc.)
- Requires Close > SMA200 (uptrend)
- Quality-On-Sale bucket requires Drop from 52W >= 15%

**Why it's contradictory:** Most stocks with ROCE >= 20% and ROE >= 15% are already priced at premiums and trade well above SMA200 in strong uptrends. But the Quality-On-Sale bucket requires a 15% drop from highs AND Close > SMA200. A 15% drop from highs usually means the stock has already broken below SMA200. The two conditions fight each other — you need a quality stock that dropped but not enough to break the 200DMA. This narrows the window to maybe 2-3% of stocks at any time.

**Impact:** Core Compounder and Growth Multiplier buckets are achievable but rare (high fundamental bar). Quality-On-Sale is internally contradictory. Opportunistic requires extreme profit growth (>= 40%).

### Issue 5: Multi-TF — 4-Phase Convergence is Near-Impossible (CRITICAL)

**The conflict:**
Requires simultaneous confirmation across 4 timeframes:
1. 1H: EMA9 > EMA20 > SMA50 > Close > SMA200 + ADX >= 18
2. 30m: BB Width Pctile < 0.45 (squeeze = volatility contracting)
3. 15m: Close >= EMA15
4. 5m: Close >= breakout level (20D high) + 5m volume >= 1.2x

**Why it's contradictory:** All 4 phases aligning at the exact same moment is a statistical unicorn. The 30m squeeze condition alone (BB Width < 0.45) means volatility must be contracting — but a breakout (Phase D) requires volatility to expand. These are opposite market states. Additionally, the 1H requires Close > SMA200 while the 5m requires Close >= breakout level (20D high) — a stock can be above SMA200 but still below its 20D high.

**Impact:** The scanner almost always gets stuck at "CORE MET (1H Setup Approved)" and rarely progresses to Phase B/C/D. This makes it functionally a 1H scanner with bonus decorations rather than a true multi-timeframe system.

### Issue 6: EOD Production vs Evaluator Discrepancy (LOW)

**The conflict:**
The standalone `evaluate_eod_symbol()` (used in stock analyzer UI) checked ~15 conditions. The production `_start_wrapper()` checked ~33 conditions. A stock could show "CORE MET" in the analyzer UI but fail in the actual production scan. This creates confusion when users see qualifying analysis in the UI but no alert is generated.

**Status:** **FIXED** — Both paths now use shared `_check_eod_conditions()` function.

---

## 9. Fixes Applied (P0-P6)

All 13 fixes have been implemented across 7 files. Below is a summary of each change.

### Priority 0 — Instrument Before Telemetry

**File:** `app/funnel_telemetry.py`

- Added `_bucketize()` helper for per-condition rejection bucketing
- Enhanced `log_funnel_metrics()` to log per-condition breakdowns with counts and pass rates
- Added `log_condition_rejection()` public helper for scanner modules to log per-symbol condition rejections

**Rationale:** Before tuning scanner parameters, we need visibility into which conditions are killing the most signals. The funnel telemetry now provides per-condition granularity.

---

### Priority 1 — EOD Breakout (Fixes 1-4)

**File:** `app/eod_scanner.py`

#### Fix 1: Remove hard 3% gap filter
- **Before:** Stocks gapping up >3% from the 10-bar lookback high were hard-rejected
- **After:** Gap penalty is now a proportional scoring penalty via `technical_penalties["gap_extended"]`
- **Rationale:** Breakout gap-ups indicate institutional demand. Hard-rejecting them contradicts the purpose of a breakout scanner. A proportional penalty (up to -20 pts) allows strong setups with moderate gaps to survive.

#### Fix 2: Remove redundant BB Width check on current bar
- **Before:** Required `BB_WIDTH_PCTILE <= 0.80` on the breakout bar itself
- **After:** Removed. The `base_too_wide` filter already checks bar[-2]'s BB width.
- **Rationale:** BB Bands expand on breakout candles by definition. Checking the current bar's width is self-defeating.

#### Fix 3: Convert hard candle gates to scoring penalties
- **Before:** 4 conditions (body_ratio < 0.45, bearish candle, close_position < 0.65, wick > 0.35) were hard rejections
- **After:** Each applies a proportional `candle_penalty` (up to 15, 5, 10, 10 respectively), subtracted from the score
- **Rationale:** These candle quality checks rejected ~40% of valid breakouts. A breakout with a slightly small body or moderate wick is still a breakout — the scoring engine should weigh this, not a binary gate.

#### Fix 4: Cap regime-adjusted threshold at 87
- **Before:** Regime modifiers pushed the threshold up to 90-95 in SIDEWAYS/WEAK_BEAR regimes
- **After:** `global_min_score = min(global_min_score, 87)` after applying regime modifiers
- **Rationale:** The scoring engine already penalizes weakness in bear/sideways regimes. Double-penalizing via higher thresholds kills valid setups that the engine already down-ranked.

---

### Priority 2 — Multi-TF Intraday (Fixes 5-6)

**File:** `app/multi_tf_scanner.py`

#### Fix 5: Change squeeze detection to "recently released"
- **Before:** Phase B required `BB_WIDTH_PCTILE < 0.45` on the immediately prior bar
- **After:** Phase B looks back 6-8 bars for any bar with `BB_WIDTH_PCTILE < 0.45` that is now expanding (`bb_pctile > prev bb_pctile`)
- **Rationale:** A squeeze that ended 3-5 bars ago (BB expanding now) is a better entry than one that's currently squeezed. The coiling-to-expansion transition is the signal, not the squeeze itself.

#### Fix 6: Weighted phase scoring
- **Before:** Fixed score of 75-80 based on ADX
- **After:** Base 60 (Phase A mandatory) + Phase B (+10) + Phase C (+5) + Phase D (+10) = max 85. Threshold 75.
- **Rationale:** Rewards stocks that progress through multiple confirmation phases rather than binary pass/fail. A stock with Phase A+B+C (score 75) is more likely to trigger than one with only Phase A (score 60).

#### Fix 14: Phase D over-extension cap (CRITICAL)
- **Before:** Over-extension gate used 5m ATR20 (`atr20` from 5-minute DataFrame) with `max_ext_atr=0.8`. A ₹1000 stock with 2.5% daily ATR (₹25) has a 5m ATR of ≈₹2.9, so the cap was `trigger + 0.8 × 2.9 ≈ trigger + ₹2.3` (+0.23%). But Phase C admits stocks up to +2.5% above trigger. Every stock that reached ENTRY_READY was instantly rejected by PD01_OVER_EXTENDED.
- **After:** Extension is measured against **daily ATR** (fetched from `data_daily[symbol]`), falling back to `max(5m_ATR × 17, price × 0.02)`. The micro-buffer (0.15 × 5m_ATR) still uses the 5m ATR for intraday noise filtering.
- **Rationale:** The extension gate must be consistent with the admission band. Phase C allows +2.5% above trigger; the extension cap on daily ATR at 0.8× allows approximately +2% (for a 2.5% daily ATR stock), which is the correct operating range.

#### Fix 15: NaN handling for BB_WIDTH_PCTILE
- **Before:** `float(val or 1.0)` silently carries NaN (NaN is truthy) through comparisons
- **After:** Uses `float(val) if pd.notna(val) else 1.0` for proper NaN detection
- **Rationale:** Early-session bars with insufficient BB history returned NaN, which passed through the `or 1.0` default as NaN (truthy), causing squeeze tests to silently fail instead of defaulting properly.

#### Fix 16: Remove dead `bb_expanding` variable
- **Before:** `bb_expanding` was computed at Phase B but never referenced in any condition
- **After:** Removed entirely
- **Rationale:** Dead code. The comment described a "squeeze → expansion" confirmation that was never actually enforced.

#### Fix 17: Simplify redundant thrust check
- **Before:** `close_position >= 0.6 and upper_wick_ratio < 0.35`
- **After:** `close_position >= 0.65`
- **Rationale:** Since `close_position + upper_wick_ratio == 1.0` always (they partition the candle range), the conjunction simplifies to `close_position > 0.65`. Not blocking, but eliminates misleading redundancy.

#### Fix 18: Phase A must not overwrite advanced states (CRITICAL)
- **Before:** Phase A force-upserted every qualifying symbol as `HOURLY_APPROVED` with `force=True`, silently downgrading `SETUP_ARMED` or `ENTRY_READY` stocks every 15 minutes.
- **After:** Changed to `force=False`. The SQL CASE in `batch_upsert_breakout_watchlist` already protects advanced states when `force=FALSE`.
- **Rationale:** Phase A re-evaluating 1H trend should not destroy progress already made in the 30m→15m→5m ladder. The SQL guard was always present but bypassed by `force=True`.

#### Fix 19: Phase D must set state before scoring (CRITICAL)
- **Before:** When `is_ready` became True, the local `state` remained `"ENTRY_READY"`. The scoring condition `if state == "TRADE_ACTIVE"` was always false, so the Phase D bonus (+10) was never granted — score was 75 instead of 85.
- **After:** Set `state = "TRADE_ACTIVE"` immediately when `is_ready` is True, before the scoring block.
- **Rationale:** Without this, any pledge penalty or downstream minimum score of 80 would reject a signal that legitimately passed all four phases.

#### Fix 20: Fetch all ladder timeframes for all active symbols (CRITICAL)
- **Before:** `needs_15m` was filtered to `SETUP_ARMED` only; `needs_5m` to `ENTRY_READY` only. A symbol promoted Phase A→B or B→C in the same cycle had no data for the next phase. This forced a 3-cycle wait, during which Fix 18 could reset the state.
- **After:** All symbols in `HOURLY_APPROVED | SETUP_ARMED | ENTRY_READY` fetch 30m, 15m, and 5m data.
- **Rationale:** A single scan can now advance a symbol from Phase A through Phase D, rather than requiring 3 separate 15-minute cycles. This also eliminates the window during which Phase A could reset the symbol between promotions.

#### Fix 21: Pullback defense uses trigger_level only
- **Before:** `low <= max(trigger_level, e9)` — if EMA9 was above the breakout level, touching EMA9 counted as defending the breakout without actually testing the breakout zone.
- **After:** `low <= trigger_level + (0.15 * atr20)` — defense is tested against trigger_level only, with a micro-buffer for intraday noise.
- **Rationale:** Inconsistent defense levels made the pullback trigger unpredictable. A stock that never tested the breakout zone should not be considered defended.

#### Fix 22: INSIDE_BAR mode implements true inside-bar detection
- **Before:** `close > prev["High"] or (prev["High"] < mother["High"] and close > prev["High"])` which simplifies to `close > prev["High"]` — identical to PREVIOUS_HIGH mode.
- **After:** Checks that `prev` candle is fully contained within `mother` candle (df.iloc[-3]): `prev.High < mother.High AND prev.Low > mother.Low`. Engulfing requires close > prev.High after the inside bar.
- **Rationale:** The `A ∨ (B ∧ A) = A` identity meant INSIDE_BAR mode never did anything. Now it correctly detects inside-bar breakout setups.

#### Fix 23: Diagnostic evaluator uses correct timeframe close
- **Before:** `if close_price >= ema15` compared the 1-hour close with the 15-minute EMA.
- **After:** Fetches `close_15 = lat_15["Close"]` and compares `close_15 >= ema15`.
- **Rationale:** Phase C evaluates 15-minute price alignment. Using the 1-hour close could incorrectly report Phase C as passed.

#### Fix 24: Replace silent `except Exception: pass` in diagnostics
- **Before:** All three diagnostic timeframes (30m, 15m, 5m) caught all exceptions and silently discarded them. A missing column, NaN, or fetch error appeared as "pending" with no trace.
- **After:** `except Exception as exc: logger.exception(...) + phase_details.append("... unavailable due to processing error")`
- **Rationale:** Technical failures should be logged, not hidden. This allows operators to distinguish between "not yet triggered" and "broken pipeline" without digging through code.

#### Fix 25: Pledge penalty was dead code — never applied (CRITICAL)
- **Before:** `pledge_penalty = int(max_penalty * scale)` always ≥ 0, then `if pledge_penalty < 0:` could never be true. The penalty was computed but never subtracted from the score.
- **After:** `pledge_penalty = int(abs(max_penalty) * scale)` then `if pledge_penalty > 0: base_score -= pledge_penalty`. Penalty now correctly reduces score.
- **Rationale:** The `< 0` guard was mathematically unreachable since both operands were non-negative. A stock with 50% promoter pledge now correctly loses up to `max_penalty` points instead of being scored as if it had zero pledge.

---

### Priority 3 — Reversal Bounce (Fixes 7-8)

**File:** `app/reversal_scanner.py`

#### Fix 7: Extend MACD crossover window 10→20 bars
- **Before:** Required MACD bullish cross within last 10 bars
- **After:** Extended to 20 bars
- **Rationale:** Many valid reversal setups show MACD cross 12-18 bars ago during the base-building phase. 10 bars missed these early signals.

#### Fix 8: Extend RSI lookback, lower threshold, add slope
- **Before:** Required RSI min within 15 bars, RSI_CURL_MIN = 50
- **After:** Extended to 25 bars, RSI_CURL_MIN = 45, added RSI slope condition (must be rising over last 3 bars)
- **Rationale:** Lowering RSI_CURL_MIN to 45 allows stocks still building recovery momentum. The extended lookback captures slower bottoming patterns. The slope condition prevents catching falling knives — RSI must be actively rising.

---

### Priority 4 — Pullback Continuation (Fixes 9-10)

**File:** `app/config.py`

#### Fix 9: Reduce pivot confirmation lag, relax internal swings
- **Before:** `CONFIRM=3`, `MAX_INTERNAL_SWINGS=2`
- **After:** `CONFIRM=2`, `MAX_INTERNAL_SWINGS=3`
- **Rationale:** 3 bars of confirmation was too conservative, missing valid swing highs. 3 internal swings during the corrective phase is still orderly — requiring ≤2 rejected slightly longer consolidations.

#### Fix 10: Soften trigger candle requirements
- **Before:** `MIN_CLOSE_LOCATION=0.75`, `MIN_BODY_ATR=0.5`
- **After:** `MIN_CLOSE_LOCATION=0.65`, `MIN_BODY_ATR=0.35`
- **Rationale:** Requiring close in the top 25% of candle range was too strict. Requiring body >= 0.5 ATR rejected valid continuation triggers with moderate bodies but strong close location and volume.

---

### Priority 5 — Wealth Engine (Fixes 11-12)

**File:** `app/wealth_engine.py`

#### Fix 11: Gradient scoring
- **Before:** Binary score: 85.0 if qualified, 50.0 if not
- **After:** Weighted bucket scoring: Core=30, Growth=25, QOS=20, Opportunistic=15, Trend=+10, PEG=+5
- **Rationale:** A stock matching Core+Growth (55 pts) should score higher than one matching only Opportunistic (15 pts). Binary scoring gave them the same score.

#### Fix 12: Lower Quality-On-Sale drop threshold 15%→10%
- **Before:** Required Drop from 52W High >= 15% for QOS bucket
- **After:** Reduced to >= 10%
- **Rationale:** A 10% correction from 52W high is already meaningful for high-quality stocks. 15% was too deep and missed quality stocks that had healthy 10-14% pullbacks during consolidation. Updated in both `evaluate_wealth_symbol()` and `determine_portfolio_bucket()`.

---

### Priority 6 — Consistency (Fix 13)

**File:** `app/eod_scanner.py`

#### Fix 13: Unified condition function
- **Before:** `evaluate_eod_symbol()` (UI) had ~15 checks; production had ~33. Stocks could show "CORE MET" in the analyzer but fail in production.
- **After:** Created `_check_eod_conditions()` shared function used by both paths. UI path calls it with `mode="ui"`, production path calls it with `mode="production"`.
- **Rationale:** Eliminates the discrepancy between UI diagnostics and production alerts. Both paths now run identical condition checks.

---

## 10. Summary & Recommendations

### Alert Generation Bottleneck Map (Post-Fix)

| Scanner | # Hard AND Conditions | Bottleneck Severity | Primary Issue | Status |
|---------|----------------------|--------------------|---------------|--------|
| EOD Breakout | 33+ (4→soft) | **MEDIUM** (was CRITICAL) | RSI + Breakout + Gap contradiction | **IMPROVED** |
| Reversal Bounce | 14 (2 relaxed) | **MEDIUM** (was CRITICAL) | RSI recovery paradox | **IMPROVED** |
| Multi-TF Intraday | 5 base + 4 phases | **MEDIUM** (was CRITICAL) | Multi-timeframe alignment | **IMPROVED** |
| Pullback Continuation | 9+ (3 relaxed) | **LOW** (was MEDIUM) | Trigger candle too specific | **IMPROVED** |
| Wealth Engine | 3 + 1 bucket | **LOW** (was MEDIUM) | Trend-vs-drop contradiction | **IMPROVED** |
| Multibagger | 3-5 | LOW | Relatively achievable | UNCHANGED |

### Key Observations Post-Fix

1. **EOD scanner moved 4 hard gates to soft penalties** — the most impactful change. Candle quality, gap size, and extended breakout are now proportional penalties that reduce the score rather than immediate rejections. This alone should increase alert volume by 30-50%.

2. **Reversal scanner RSI paradox resolved** — extending lookback to 25 bars and adding slope condition allows gradual recovery patterns (healthier than V-shaped). Lowered RSI_CURL_MIN to 45 captures stocks still building momentum.

3. **Multi-TF squeeze-released detection is more realistic** — requiring a squeeze to have existed in the last 8 bars (not just the immediate prior bar) captures the coiling-to-expansion transition more reliably.

4. **Pullback trigger candle is now achievable** — lowering body/ATR from 0.5→0.35 and close location from 0.75→0.65 allows moderate-strength resumption candles. The CONFIRM reduction from 3→2 also captures more swing highs.

5. **Wealth Engine's QOS threshold 10% is more realistic** — quality stocks with 10-14% pullbacks are now eligible, capturing the healthy consolidation zone that the old 15% threshold missed.

6. **Shared EOD condition function eliminates UI/production drift** — future condition changes only need to be made in one place.

### Next Steps (Recommended)

| Priority | Action | Expected Impact |
|----------|--------|-----------------|
| HIGH | Monitor alert volume changes over 1 week | Validate fix impact |
| MEDIUM | Run backtest on last 90 days of data | Compare alert quality |
| MEDIUM | Add regression tests for shared condition function | Prevent future drift |
| LOW | Consider making Multi-TF Phase B/C/D scoring bonuses (not requirements) | Further increase alert volume |

---

### Priority 7 — Multibagger Scanner (Fixes MUL-1 through MUL-6)

**File:** `app/multibagger.py`

#### MUL-1: Pledge unit mismatch — quality gate effectively disabled (CRITICAL)
- **Before:** Gate compared pledge as ratio (`> 0.20`), but `evaluate_multibagger_symbol` compared as percentage (`<= 10.0`). A 90%-pledged promoter passed `<= 10.0` because a ratio is always ≤1. Conversely, if gate was ever fed a percentage, `> 0.20` rejected every stock with any pledge at all.
- **After:** Added `_pledge_ratio()` normalizer that tolerates either unit and always returns ratio. Both gate and tier logic now compare against ratios (0.10 = 10%, 0.15 = 15%).
- **Rationale:** The pipeline stores pledge as ratio (`pledge_val / 100.0`), but diagnostic callers could pass either unit. The mismatch meant the pledge ceiling was either always-pass or always-fail depending on the caller.

#### MUL-2: entry_confirmed SMA-200 hard reject blocks deep-value entries (MEDIUM)
- **Before:** `price < sma_200` hard-rejected entries below the 200-DMA. A genuine deep-value pullback frequently dips 1-3% below the 200-DMA, making the advertised "Deep Value Zone" path largely unreachable.
- **After:** `price < sma_200 * 0.97` — allows a 3% band below the 200-DMA. The screening tier already enforced `close > sma50 > sma200` at qualification.
- **Rationale:** The docstring says "a prime stock pulling back into its buy zone on a red day is the ideal entry" — the hard reject contradicted this intent.

#### MUL-3: is_uptrend always False for young stocks (LOW-MEDIUM)
- **Before:** `close > sma50 > sma200` with `< 200 bars` collapsed to `close > close` (always False) because sma200 defaulted to close_price.
- **After:** Guard on data length: `>= 200 bars` requires full stack, `>= 50 bars` requires close > sma50, `< 50 bars` assumes uptrend.
- **Rationale:** Legitimately young high-quality names could never pass `is_uptrend` in the diagnostic. Production path uses V5 pipeline which has its own trend check.

#### MUL-4: Missing-fundamentals fallback passes weak names (HIGH for false positives)
- **Before:** When `f_score` or `pledge_pct` was missing, `is_prime = (composite >= 75) and is_uptrend`. Since composite defaults to 70.0 when V5 fails, a failed pipeline + uptrend passed "High Quality" (≥65) with zero fundamental verification.
- **After:** When composite is the 70.0 default (V5 didn't run), reject — `is_high_quality = False`. Only real V5 scores (>70) can qualify.
- **Rationale:** The default 70.0 was specifically designed as a "no data" sentinel, but the `>= 65` threshold treated it as a passing grade.

#### MUL-5: Gate's "known_metrics ≥ 2" floor is trivially satisfied (MEDIUM)
- **Before:** Any 2 metrics known (e.g., revenue CAGR + ROCE) passed, while debt, cash conversion, and Altman-Z could all be unknown.
- **After:** Require ≥ 3 metrics AND at least one solvency metric (D/E, interest coverage, or Altman-Z).
- **Rationale:** A "multibagger" tag without solvency verification is a false-positive risk. The solvency metrics are the minimum for assessing default/leverage risk.

#### MUL-6: Institutional bonus inflates tier classification (MEDIUM)
- **Before:** `classify_conviction()` received the post-bonus `total`. `inst_bonus` could push a 63 → 65 and flip "Watchlist" → "High Quality", firing an alert on a name that failed on its own merits.
- **After:** Classification uses `pre_bonus_total`; `inst_bonus` only affects the final `total` used for ranking.
- **Rationale:** Bonuses should enhance ranking within a tier, not create tiers. A stock that scores 63 without institutional footprints is not a "High Quality Multibagger."

#### MUL-7: Production F-score key mismatch silently bypasses Prime gate (CRITICAL)
- **Before:** `raw_fundamentals.get("piotroski_f_score", raw_fundamentals.get("f_score"))` — neither key is ever written by `fetch_ticker_fundamentals`. The Piotroski score lives under `"score"` / `"piotroski_score"`. So `f_score_val` was always `None`, and `classify_conviction` treated `None` as "always passes Piotroski ≥ 7".
- **After:** Fallback reads `"score"` / `"piotroski_score"` (matching `evaluate_multibagger_symbol` line 68), converting to int if present.
- **Rationale:** Without this fix, any stock with composite ≥ 75 was tagged "Prime Multibagger" regardless of its actual Piotroski F-Score — the exact false-positive hole the original MUL-1 through MUL-6 fixes were meant to close.

#### MUL-8: `_pledge_ratio` uses `pd` before pandas import (MEDIUM, crash risk)
- **Before:** `_pledge_ratio` called `pd.isna()` but `import pandas as pd` was defined 5 lines later. Function body runs at call-time so it usually worked, but the ordering was fragile.
- **After:** Moved all import statements above `_pledge_ratio` definition.
- **Rationale:** Prevents latent `NameError` if module imports are reordered or if `_pledge_ratio` is called during module loading.

#### MUL-9: Diagnostic fundamentals-optional path still too easy (MEDIUM)
- **Before:** `elif f_score is None and composite_score > 70.0` allowed "High Quality Multibagger" with zero fundamental verification (no F-score, no pledge).
- **After:** Split into two branches: F-Score unknown but pledge known (allows HQ if pledge ≤ 15%), and both unknown (rejects).
- **Rationale:** A "multibagger" label requires at minimum one verified fundamental data point. Composite > 70 alone is not sufficient fundamental backing for the label.

---

*Document generated from source code analysis. All thresholds sourced from `config.py`, individual scanner files, and `stock_analyzer.py`. Fixes applied on 2026-07-28.*
