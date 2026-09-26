# ELITE BREAKOUT SYSTEM — COMPREHENSIVE SCANNER STATUS REPORT
**Audit Timestamp:** 2026-09-26 10:00:00 IST  
**System Architecture:** Asynchronous Multi-Timeframe Event-Driven Algorithmic Trading Platform  
**Governance Standard:** 5-Gate Promotion System & Pre-Push Zero-Defect Parity  

---

## 1. Master Scanner Inventory Matrix

Below is the complete census of all 19 scanners and execution engines in the codebase, categorized by operational status.

| # | Scanner Identifier | Engine / Source File | Execution Cadence | Timeframe | Category | Operational Status | Disposition |
|---|---|---|---|---|---|---|---|
| 1 | **DAILY_BUILDER** | `app/daily_builder.py` | Daily 05:00 IST | 1D | Core Pipeline | **ACTIVE** | Retained (Production Core) |
| 2 | **MULTI_TF** | `app/multi_tf_scanner.py` | Every 15m (09:30–15:30 IST) | 15M / 1H | Intraday Breakout | **ACTIVE** | Retained (Production Core) |
| 3 | **MULTI_TF_5M** | `app/multi_tf_scanner.py` | Every 5m (09:35–15:25 IST) | 5M | Intraday Monitor | **ACTIVE** | Retained (Production Core) |
| 4 | **EOD** | `app/eod_scanner.py` | Daily 18:30 IST (Post-Bhavcopy) | 1D | EOD Breakout | **ACTIVE** | Retained (Production Core) |
| 5 | **REVERSAL** | `app/reversal_scanner.py` | Daily 18:30 IST (Post-Bhavcopy) | 1D | Mean Reversion | **ACTIVE** | Retained (Production Core) |
| 6 | **PULLBACK** | `app/pullback_pipeline.py` | Daily 18:30 IST (Post-Bhavcopy) | 1D | Support / EMA | **ACTIVE** | Retained (Production Core) |
| 7 | **ACCUMULATION** | `app/accumulation_scanner.py` | Daily 18:35 IST (Post-Bhavcopy) | 1D | VCP / Institutional | **ACTIVE** | Retained (Production Core) |
| 8 | **TECHNICAL** | `app/technical_scanner.py` | Daily 18:15 IST (Post-Close) | 1D | Pattern / Indicator | **ACTIVE** | Retained (Production Core) |
| 9 | **TECHNICAL_INTRADAY** | `app/technical_scanner_intraday.py` | Every 15m (09:16–15:30 IST) | 15M | Intraday Technical | **ACTIVE** | Retained (Production Core) |
| 10 | **WEALTH_ENGINE** | `app/wealth_engine.py` | Daily 06:00 & 17:00 IST | 1W / 1M / 1D | Long-Term Compounder | **ACTIVE** | Retained (Production Core) |
| 11 | **MULTIBAGGER** | `app/multibagger.py` | Daily 17:30 IST | 1D / Fundamental | Growth Momentum | **ACTIVE** | Retained (Production Core) |
| 12 | **PERFORMANCE_TRACKER** | `app/performance_tracker.py` | Every 5m (09:15–15:30 IST) | 5M / 1D | Execution & Exit | **ACTIVE** | Retained (Production Core) |
| 13 | **MULTIBAGGER_EXIT** | `app/multibagger_state_machine.py` | Every 15m (09:15–15:30 IST) | 15M | Exit Monitor | **ACTIVE** | Retained (Production Core) |
| 14 | **WEALTH_EXIT** | `app/wealth_engine.py` | Every 5m (09:15–15:30 IST) | 5M | Trailing SL / CMP | **ACTIVE** | Retained (Production Core) |
| 15 | **PLEDGE_WORKER** | `app/pledge_worker.py` | Continuous (Daily Refresh) | Daily | Risk Intelligence | **ACTIVE** | Retained (Production Core) |
| 16 | **AI_WORKER** | `app/ai_worker.py` | Continuous (Sat-Sun Active) | Event-driven | AI Narrative | **ACTIVE** | Retained (Production Core) |
| 17 | **SHORT_COVERING_5M** | `app/short_covering/short_covering_scanner.py` | Every 5m (Decommissioned) | 5M | Intraday Ignition | ❌ **STOPPED / DEAD** | **EXCISE COMPLETELY** |
| 18 | **SHORT_COVERING_EOD** | `app/short_covering/short_position_detector.py` | Daily 09:05 IST (Decommissioned) | 1D | F&O Buildup | ❌ **STOPPED / DEAD** | **EXCISE COMPLETELY** |
| 19 | **MOMENTUM_THRUST_REVERSAL_H0** | `app/momentum_thrust_h0_engine.py` | Inactive Research Engine | 1D | Research Artifact | ❌ **STOPPED / DEAD** | **EXCISE COMPLETELY** |

---

## 2. Detailed Technical Profiles of Active Scanners

### 2.1 Core Ingestion & Watchlist Construction
* **DAILY_BUILDER (`app/daily_builder.py`)**
  * **Role:** Constructs the daily trading universe for all downstream scanners.
  * **Execution:** Scheduled at 05:00 AM IST on market days.
  * **Mechanism:** Screens NSE 500 + active F&O equities for liquidity ($> \text{₹5 Cr}$ daily turnover), structural consolidation, delivery volume accumulation, and volatility compression.
  * **Output:** `data/daily_watchlist.parquet` and memory cache in `watchlist_cache.py`.

### 2.2 Intraday Multi-Timeframe Engines
* **MULTI_TF (`app/multi_tf_scanner.py`)**
  * **Role:** Dual-cadence intelligence scanner detecting multi-timeframe alignment across 1H trend, 15m breakout, and 5m trigger.
  * **Execution:** Closed-candle aligned at 15m boundaries (09:30, 09:45, 10:00... 15:15 IST) with a +20s buffer.
  * **Mechanism:** Screens watchlist stocks for 1H trend alignment, 15m consolidation breakout, relative volume expansion ($\ge 1.5\text{x}$), and arms valid setups.
* **MULTI_TF_5M (`app/multi_tf_scanner.py`)**
  * **Role:** Lightweight confirmation monitor for stateful ARMED candidates.
  * **Execution:** Intermediate 5m boundaries (09:35, 09:40, 09:50, 09:55... 15:25 IST).
  * **Mechanism:** Evaluates only stocks in the `ARMED` pool for trigger bar confirmation ($<3\text{s}$ execution time).

### 2.3 Post-Market Daily Scanners (Post-Bhavcopy)
* **EOD (`app/eod_scanner.py`)**
  * **Role:** Flagship classical breakout scanner operating on official verified exchange Bhavcopy.
  * **Execution:** Scheduled at 18:30 IST after Bhavcopy delivery and verification.
  * **Mechanism:** Identifies 52-week highs, multi-month consolidation breakouts, pocket pivots, and volume expansion with tight ATR-based risk control.
* **REVERSAL (`app/reversal_scanner.py`)**
  * **Role:** Mean reversion and structural swing reversal engine.
  * **Execution:** Scheduled at 18:30 IST in the evening batch.
  * **Mechanism:** Identifies oversold extremes at key support levels, capitulation volume spikes, and candle reversal formations (hammer, bullish engulfing).
* **PULLBACK (`app/pullback_pipeline.py`)**
  * **Role:** Trend-continuation pullback scanner.
  * **Execution:** Scheduled at 18:30 IST in the evening batch.
  * **Mechanism:** Identifies leading stocks in established uptrends pulling back to 20 EMA, 50 EMA, or prior breakout pivots with drying volume.
* **ACCUMULATION (`app/accumulation_scanner.py`)**
  * **Role:** Institutional accumulation and Volatility Contraction Pattern (VCP) detector.
  * **Execution:** Scheduled at 18:35 IST after Bhavcopy verification.
  * **Mechanism:** Screens delivery percentages ($>50\%$), contracting daily ranges (contractions 1 to 4), and volume dry-ups prior to major stage-2 breakouts.

### 2.4 Technical & Indicator Scanners
* **TECHNICAL (`app/technical_scanner.py`)**
  * **Role:** Broad technical pattern scanner on daily timeframes.
  * **Execution:** Scheduled at 18:15 IST post-market close.
  * **Mechanism:** Calculates classical chart patterns (cup & handle, double bottoms, ascending triangles), RSI momentum crosses, and MACD divergences.
* **TECHNICAL_INTRADAY (`app/technical_scanner_intraday.py`)**
  * **Role:** Real-time intraday 15m technical pattern scanner.
  * **Execution:** Every 15 minutes during market hours (09:16 to 15:30 IST).
  * **Mechanism:** Evaluates real-time 15m candles for intraday bull flags, ascending triangle breaks, and moving average crossovers.

### 2.5 Fundamental & Long-Term Compounding Engines
* **WEALTH_ENGINE (`app/wealth_engine.py`)**
  * **Role:** Multi-year compounding portfolio engine.
  * **Execution:** Initial run at 06:00 AM IST, evening rebalance at 17:00 IST.
  * **Mechanism:** Combines quarterly fundamental growth (sales growth, profit margins, ROE $>15\%$), debt/equity filters, and monthly/weekly structural chart health.
* **MULTIBAGGER (`app/multibagger.py`)**
  * **Role:** Small/Mid-cap asymmetric upside detector.
  * **Execution:** Daily at 17:30 IST.
  * **Mechanism:** Screens for earnings acceleration ($>25\%$ YoY), institutional sponsorship expansion, and low-base breakouts.

### 2.6 Active Lifecycle & Exit Monitors
* **PERFORMANCE_TRACKER (`app/performance_tracker.py`)**
  * **Role:** Centralized trade outcome tracker and active exit engine.
  * **Execution:** Every 5 minutes during market hours (09:15 to 15:30 IST).
  * **Mechanism:** Evaluates live CMP against stop loss, target 1, target 2, and trailing stop levels across all open scanner positions.
* **MULTIBAGGER_EXIT (`app/multibagger_state_machine.py`)**
  * **Role:** Dedicated position state machine for multibagger holdings.
  * **Execution:** Every 15 minutes during market hours.
  * **Mechanism:** Manages multi-week trailing stops (10-week MA failure, momentum loss, quarterly earnings breakdown).
* **WEALTH_EXIT (`app/wealth_engine.py`)**
  * **Role:** Trailing stop and CMP valuation monitor for Wealth Engine positions.
  * **Execution:** Every 5 minutes during market hours.
  * **Mechanism:** Updates trailing stops and detects structural trend breaks.

### 2.7 Background Auxiliary Workers
* **PLEDGE_WORKER (`app/pledge_worker.py`):** Monitors promoter share pledge levels from BSE/NSE filings.
* **AI_WORKER (`app/ai_worker.py`):** Background worker synthesizing qualitative news, concalls, and risk factors using Gemini LLM.

---

## 3. Detailed Forensic Profiles of Stopped / Decommissioned Scanners

### 3.1 SHORT_COVERING_5M (Decommissioned)
* **Status:** ❌ **PERMANENTLY DECOMMISSIONED & SILENCED**
* **Root Cause & Forensic Evidence:**
  1. Real Upstox tick and 5M bar data proved that contemporaneous intraday open interest (OI) contraction does NOT provide statistically significant alpha over ordinary Price + Volume breakout ($\Delta\text{Exp} = +0.116\text{R}$, $p = 0.3849$).
  2. The OI contraction condition filtered out $68.6\%$ of actionable setups while adding zero predictive drift.
  3. Execution diagnostic proved that on 5-minute bars, $T+1$ execution, adverse slippage, and spread friction impose a structural drag of $\approx 0.08\text{R}$ to $0.15\text{R}$, capping 5M breakout expectancy at $-0.20\text{R}$ regardless of parameter tuning.
* **Codebase Presence:** Lingering scheduler threads in `app/main.py`, models in `app/short_covering/`, health entries in `app/database.py`, and dashboard options.

### 3.2 SHORT_COVERING_EOD (Decommissioned)
* **Status:** ❌ **NOT CERTIFIED / PERMANENTLY SILENCED**
* **Root Cause & Forensic Evidence:**
  1. In the canonical D1–D10 multi-session tournament using verified near+next month futures OI, all OI-conditioned variants failed the mandatory Gate 5 holdout criterion ($95\%\text{ Bootstrap } CI_{low} > 0.0\text{R}$).
  2. Counterfactual analysis proved that Price + High Prior OI ($p=0.89$) and Price + OI Contraction ($p=0.78$) perform identically or worse than ordinary unconditioned price momentum.
  3. Small positive sample point estimates (e.g. D3 with $+0.439\text{R}$) were textbook underpowered artifacts ($N=3$, $95\%\text{ CI } [-1.000\text{R}, +1.500\text{R}]$).
* **Codebase Presence:** `app/short_covering/short_position_detector.py`, `app/main.py` (09:05 AM scheduler loop), `app/database.py`.

### 3.3 MOMENTUM_THRUST_REVERSAL_H0 (Decommissioned)
* **Status:** ❌ **DECOMMISSIONED RESEARCH ARTIFACT**
* **Root Cause:** Explicitly retired research module (`DECOMMISSIONED: bool = True` in `app/momentum_thrust_h0_engine.py`). Superseded by certified `REVERSAL` scanner.

---

## 4. Decommissioning & Complete Codebase Removal Plan

To cleanly excise all dead/stopped scanners without disrupting any of the 16 active production engines:

### Step 1: Delete Dead Scanner Modules
* Remove `app/short_covering/` directory completely (9 files).
* Remove `app/short_covering_config.py`.
* Remove `app/certification/adapters/short_covering_adapter.py`.
* Remove `app/momentum_thrust_h0_engine.py`, `app/portfolio_h0_simulation.py`, `app/forward_holdout_pipeline.py`.
* Remove `tests/short_covering/` and `tests/test_short_covering_c5_production.py`.

### Step 2: Clean Production Scheduler & Main Entrypoint (`app/main.py`)
* Remove `SHORT_COVERING_EOD` from `run_all_seven_scanners_non_market_boot`.
* Remove `last_short_covering_5m` and `last_short_covering_eod_date` state variables.
* Remove 09:05 AM `SHORT_COVERING_EOD` scheduler block.
* Remove market-hours `SHORT_COVERING_5M` 5-minute scheduler loop.
* Remove `SHORT_COVERING`, `SHORT_COVERING_EOD`, `SHORT_COVERING_5M` from `TRIGGER_MAP` and `LOCK_MAP`.
* Remove `_trigger_short_covering_eod`, `_trigger_short_covering_5m`, and `_trigger_short_covering` functions.

### Step 3: Clean Health & Database Mappings (`app/database.py`)
* Remove `SHORT_COVERING_EOD` and `SHORT_COVERING_5M` from `schedule_map`.
* Delete `short_covering_watchlist` table initialization from `init_db()`.

### Step 4: Clean Dashboard & Certification Registries
* Remove short covering options from `app/admin_dashboard.html`.
* Remove `SHORT_COVERING` adapters and models from `app/certification/registry.py`, `app/certification/models.py`, `app/certification/replay.py`, and `app/certification/test_certification.py`.
* Clean `app/sl_target_helper.py` and `app/lock_utils.py`.

### Step 5: Full Syntax & Symbol Sanity Verification
* Execute `python3 -m py_compile` across all modified files.
* Test import execution on all production entrypoints.
