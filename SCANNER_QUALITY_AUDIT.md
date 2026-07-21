# Elite Breakout System: Comprehensive Scanner & Exit Audit

This document provides a line-by-line architectural and logic audit of all 5 core scanners in the Elite Breakout System (`EOD`, `Multi-TF`, `Reversal`, `Multibagger`, `Wealth`). 

The focus is on data ingestion, condition achievability (hidden impossible logic), target generation, and exit/stop-loss mechanics.

---

## 1. Multi-Timeframe Scanner (`multi_tf_scanner.py`)

### Data Ingestion & Alerts
- **Ladder Architecture:** Uses a Phase A (1H) → B (30m) → C (15m) → D (5m) sequential funnel. This efficiently filters out noise and reduces API calls.
- **Pullback Logic Gap (Impossible Condition):** In Phase D, the `pullback` trigger logic requires `close > float(prev["High"])`. A pullback setup by definition triggers when a stock drops (pulls back) into support (like EMA9 or VWAP) and then reverses. If it drops sharply, the previous candle was a large red candle. Demanding the very next 5-minute candle to engulf and close *above* the previous high is an extremely rare and difficult condition. Real pullback entries usually trigger on a close above the *open* or an inside bar breakout. Requiring `close > prev['High']` makes the pullback trigger function more like a "thrust" rather than a true pullback, significantly reducing valid entries.

### Exits & Targets
- **Target Generation:** Uses `MULTI_TF` mode in `sl_target_helper.py`. Implements `TrendExtensionStrategy` and relies heavily on Fib Extensions (127%, 162%) and ATR Projections.
- **SL Generation:** Utilizes the new VWAP-anchored stop loss and swing low clustering. This provides a dynamic, institutional-grade stop that adjusts to intraday volatility.
- **Risk:** High-quality setup. The main vulnerability is early stop-outs if the 5m ATR is unusually compressed prior to the breakout.

## 2. Reversal Scanner (`reversal_scanner.py`)

### Data Ingestion & Alerts
- **Drop Band:** Requires a drop from 52W high of 20% to 45% (lowered to 15% for quality stocks). This is a highly realistic sweet spot that avoids "falling knives" (60%+ drops).
- **MACD Normalization Bias:** The scoring engine normalizes MACD using `mh_pct = (float(macd_hist) / float(close_price)) * 100.0`. While this attempts to remove large-cap bias, it introduces a severe penalty for high-priced stocks (e.g., MRF, Page Industries). A stock priced at ₹30,000 will have an infinitesimally small `mh_pct`, failing to meet the `0.10` or `0.03` thresholds, even during massive momentum shifts. This silently excludes high-priced large caps from high reversal scores.

### Exits & Targets
- **Target Generation:** Uses `MeanReversionStrategy`. Targets are intelligently set to structural reversion levels: Bollinger Band Mid, 38.2%/50% Retracements of the decline, SMA50, and SMA200.
- **SL Generation:** Applies the widest ATR buffer (1.0x ATR) to account for the volatility of beaten-down stocks.
- **Hidden Conflict:** The `sl_target_helper.py` enforces a `MIN_NATURAL_RR` of 2.0 for `REVERSAL`. Because the SL is extremely wide (1.0x ATR), the nearest target (e.g., EMA20 or 38.2% retracement) often fails to provide a 2.0 RR. This results in the `NO_VALID_STRUCTURAL_TARGET` rejection for highly volatile stocks, even when the mean reversion setup is valid.

## 3. Multibagger Scanner (`multibagger.py`)

### Data Ingestion & Alerts
- **Quality Gates:** Employs rigorous fundamental checks (ROCE > 10%, Debt/Equity < 2.0, Positive Revenue CAGR, Altman-Z solvency).
- **Entry Stabilization:** Requires the price to be above the 200-DMA (`price >= price_data.sma_200`) and relative volume expansion. This ensures we don't buy into a fundamentally sound company that is in a technical death spiral.

### Exits & Targets
- **Target Generation:** None. (Intentional long-term hold philosophy).
- **Exit Logic (Dynamic Stops):** Implements a highly robust Catastrophic Stop tiered by Market Cap (20% Large, 25% Mid, 30% Small) and adjusted for Trend Health (tightened by 2% if deeply below 200DMA). 
- **Hidden Gap (Suspended Trading):** The exit monitor skips fundamental exits if Yahoo Finance returns empty data (`cqs = 15.0; is_invalid = False;`). If a stock is suspended by the exchange or permanently delisted, it will silently remain in the portfolio forever without triggering a sell alert. It needs a "stale price data" timeout check (e.g., exit/alert if price hasn't updated in 10 trading days).

## 4. Wealth Engine (`wealth_engine.py`)

### Data Ingestion & Alerts
- **Portfolio Bucketing:** Maps stocks into Core, Growth, Quality-On-Sale, and Opportunistic buckets with strict fundamental requirements.
- **NaN Handling:** The recent implementation of `_safe_num` resolved widespread silent failures caused by Pandas `NaN` values evaluating as truthy.

### Exits & Targets
- **Target Generation:** Uses `Hold_Score` (0-100) instead of fixed price targets.
- **Exit Logic (Regime-Aware RS):** The exit threshold for Relative Strength (RS) adjusts dynamically based on the macro regime (`-55` for BEAR, `-60` for STRONG_BEAR). 
- **Bear Market Protection:** Crucially, an RS breakdown in a bear market *only* triggers an exit if the `Hold_Score < 50` or `price < sma_200`. This prevents the algorithm from panic-selling prime compounders during a broad market crash where all equities lose RS to cash. This logic is mathematically sound and perfectly aligned with wealth preservation principles.
- **Drawdown Circuit Breaker:** Instant Sell if loss exceeds 20%, Sell Review if loss exceeds 10%.

## 5. EOD Scanner (`eod_scanner.py`)

### Data Ingestion & Alerts
- **Batch Processing:** Utilizes `chunk_iterable` to process large watchlists without memory spikes.
- **Climax Top Filter:** Successfully filters out operator traps where a massive volume spike corresponds with a huge upper wick, preventing buying into distribution events.

### Exits & Targets
- **Target Generation:** Uses `ClusterConsensusStrategy`. Identifies clusters of resistance and uses the nearest strong level.
- **SL Generation:** Standard structural swing low with a 0.75x ATR buffer. 

---

## Recommendations & Next Steps

1.  **Fix Multi-TF Pullback Logic:** Change the `close > prev['High']` requirement in Phase D to a more realistic micro-reversal check (e.g., `close > open` and `close > prev['Low']`).
2.  **Fix Reversal MACD Bias:** Adjust the `mh_pct` formula in `reversal_scanner.py` to use a log scale or standard deviation rather than a pure percentage of price to prevent penalizing high-priced stocks.
3.  **Adjust Reversal RR Requirement:** Lower the `MIN_NATURAL_RR` for Reversals to 1.5, or allow dynamic targets to stretch slightly higher to accommodate the wide 1.0x ATR stop loss.
4.  **Add Delisting/Suspension Check to Multibagger:** Implement a check in `run_exit_monitor` that flags a position for review if price data has been stale for >10 days.

