  # ELITE BREAKOUT SYSTEM — SYSTEM SPECIFICATION & USER/ADMIN GUIDE

  > **Document Class:** User & Admin Operational Manual
  > **Status:** Canonical Master Guide for system functionality, trading rules, and dashboard operations.
  > **Target File:** `docs/SYSTEM_SPECIFICATION.md`
  > **Last Synchronized:** 2026-07-25 (v8.4.4 Master Sync)

  ---

  # 1. EXECUTIVE OVERVIEW & SYSTEM PURPOSE

  The **Elite Breakout System** is an autonomous, quantitative trading platform engineered specifically for the Indian Equity Markets (NSE & BSE). The system systematically scans, ranks, filters, monitors, and manages high-probability momentum breakouts, mean-reversion oversold bounces, trend pullbacks, and long-term fundamental compounders.

  ## Core Capabilities
  - **6 Specialized Quantitative Scanning Engines**: EOD Breakout, Multi-Timeframe Intraday (Multi-TF), Reversal, Pullback Pipeline, Wealth Engine, and Multibagger Engine.
  - **3 Real-Time Glassmorphic Dashboards**: User Dashboard, Admin Dashboard, and Performance Tracker Dashboard.
  - **Dynamic Risk & Target Management**: Automated initial stop loss calculation, structural support placement, trailing stop loss management, multi-target profit booking (T1, T2, T3, T4), and exit alerting.
  - **Omnichannel Notification Engine**: Real-time signal delivery via Telegram Channels, Web Push Notifications (VAPID), and In-App Portal Alerts.
  - **Autonomous 24/7 Execution**: Self-healing scheduler operating around NSE trading hours (09:15 to 15:30 IST) and evening post-market Bhavcopy publication windows.

  ---

  # 2. SCANNER SUITE & STRATEGY SPECIFICATIONS

  The system operates six distinct scanning engines, each targeting a specific market setup:

  ## 2.1 EOD Breakout Scanner (`app/eod_scanner.py`)
  - **Market Objective**: Captures daily momentum breakouts from tight consolidation bases after market close (post-18:00 IST).
  - **Setup Pattern**: Stocks forming horizontal resistance or Bollinger Band squeezes that break out with strong price close (`Close > PRIOR_20D_HIGH`) and volume expansion.
  - **Key Eligibility & Quality Gates**:
    1. Price floor: `Close >= ₹100.0`
    2. Data sufficiency: $\ge 50$ historical daily bars (to support IPOs and new listings)
    3. Bullish candle: `Close > Open`, Body Ratio $\ge 45\%$, Close Position $\ge 65\%$ of candle range, Upper Wick $\le 35\%$
    4. Volume surge: Volume Ratio $\ge 1.8\text{x}$ vs **20-day Median Volume** baseline (outlier-resistant baseline)
    5. ATR expansion: Candle Range / 20-day ATR $\ge 0.9$ (`MIN_ATR_EXPANSION_RATIO = 0.9`)
    6. Non-extended ATR gate: $(\text{Close} - \text{EMA}_{20}) / \text{ATR}_{20} \le 1.5$
    7. Base tightness: Bollinger Band Width Percentile $\le 80\text{th percentile}$
    8. Trend alignment: $\text{Close} > \text{SMA}_{50} > \text{SMA}_{200}$ (or reduced trend gate for 50-199 bar symbols) and $\text{Close} > \text{EMA}_{20}$
    9. Scoring Modifiers: **+5 pts** clean 50D-high breakout bonus; **-5 pts** young listing penalty ($< 200$ bars)
    10. Minimum Score: Composite Score $\ge 82$ out of 100+
  - **Candidate Truncation**: Evaluates all watchlist candidates, accumulates setups across universe chunks, and persists ONLY the **Top 10 ranked candidates** by composite score.

  ## 2.2 Multi-Timeframe (Multi-TF) Scanner (`app/multi_tf_scanner.py`)
  - **Market Objective**: Captures intraday momentum bursts where long-term trends align with short-term intraday triggers.
  - **Schedule**: Candle-aligned 15-minute intervals (`:00`, `:15`, `:30`, `:45`) between 09:30 AM and 14:45 PM IST during market hours.
  - **4-Stage Timeframe Cascade**:
    1. **Phase A (1H Trend Filter)**: Evaluates 3-month Hourly data (~437 bars). Mandates $\text{EMA}_9 > \text{EMA}_{20} > \text{SMA}_{50}$, $\text{Close} > \text{EMA}_{200}$ (or reduced trend gate for 50-199 bar symbols), and $\text{ADX}_{14} \ge 18$.
    2. **Phase B (30m Alignment)**: Validates 30-minute trend slope and volume expansion.
    3. **Phase C (15m Alignment)**: Validates 15-minute consolidation breakout level.
    4. **Phase D (5m Trigger)**: Decoupled execution triggers:
      - *Thrust Mode*: Price breaks local 5m highs with strong volume confirmation near trigger level.
      - *Pullback Mode*: Breakout level or EMA9 is tested/defended, followed by a strong bullish rejection candle (close position $\ge 0.60$) with volume.
    5. **Late-Session Entry Cutoff**: New Phase D entries are blocked after **14:15 IST** to eliminate late-day slippage and MOC imbalances.
  - **Minimum Score**: Composite Score $\ge 78$ out of 100. Minimum Risk-Reward Ratio $\ge 1.5$.

  ## 2.3 Reversal Scanner (`app/reversal_scanner.py`)
  - **Market Objective**: Detects oversold mean-reversion bounce setups on high-quality companies that have suffered a sharp price correction.
  - **Setup Pattern**: Quality stocks returning from oversold territory with RSI curling upward and MACD making a bullish histogram crossover.
  - **Key Eligibility & Quality Gates**:
    1. **Drop Band Gate**: Stock must be **20.0% to 45.0% below its 52-week high** (`MAX_DROP_BELOW_SMA200 = 20.0`).
    2. **Trend Structure Reclaim**: $\text{Close} \ge \text{SMA}_{50}$ (mandatory trend recovery gate).
    3. **Oversold RSI Curl**: RSI was $\le 38$ within the lookback window (`REVERSAL_RSI_LOOKBACK = 15`, `REVERSAL_RSI_MAX = 38`) and current RSI is $\ge 50$.
    4. **MACD Momentum**: Bullish MACD histogram crossover occurring within the last 10 trading bars.
    5. **Volume Confirmation**: Volume Ratio $\ge 2.0\text{x}$ 20-day average. 20-bar average volume $\ge 300,000$ shares.
    6. **Two-Tier Cooldown Architecture**: 
      - *Tier 1 (7-Day Alert Dedup)*: `ALERT_COOLDOWN_MINUTES["REVERSAL"] = 10080` prevents duplicate alert spam for active setups.
      - *Tier 2 (40-Day Fallen Knife Defense)*: `REVERSAL_COOLDOWN_TRADING_DAYS = 40` blocks symbols that recently stopped out from re-triggering for 40 trading days (matching holding lifecycle).
    7. **Macro Regime Dampening**: In `STRONG_BEAR` macro regimes, the minimum score threshold is elevated to **90 pts** (vs normal 62 pts).
    8. **Minimum Score**: Composite Score $\ge 62$ out of 100+ (90 in `STRONG_BEAR`). Reward Potential $\ge 3.0R$ (T3-based) and Natural Risk-Reward Ratio $\ge 2.0R$ (T1-based).

  ## 2.4 Pullback Pipeline (`app/pullback_pipeline.py`)
  - **Market Objective**: Identifies orderly, low-risk pullback entries within established strong uptrends.
  - **Regime Constraint**: Automatically suspended during `STRONG_BEAR` macro market regimes.
  - **4-Phase Cascade**:
    1. **Uptrend Gate**: $\text{Close} > \text{SMA}_{50} > \text{SMA}_{200}$.
    2. **Pivot & Impulse Wave**: Identifies recent swing high and validates impulse wave height ($\ge 8.0\%$, `MIN_IMPULSE_GAIN_PCT = 8.0`).
    3. **Orderly Pullback Structure**: Pullback depth must be between **23.6% and 61.8% Fibonacci retracement** of the impulse wave, accompanied by clear **volume contraction** (volume declining during pullback bars).
    4. **Resumption Trigger**: Bullish reversal bar closing above prior high/open (`PREVIOUS_HIGH`, `PREVIOUS_OPEN`, or `INSIDE_BAR`).
    5. **Sanitized Evidence Bonus**: $+3$ pts if stock triggered an active EOD alert in last 30 days; $+2$ pts if stock triggered an active Multibagger/Multi-TF alert (`only_active=True`, excluding stopped-out alerts).
    6. **Minimum Score**: Composite Score $\ge 75$ out of 100+ (base threshold 75 + regime modifier). Minimum Risk-Reward Ratio $\ge 2.0$.

  ## 2.5 Wealth Engine (`app/wealth_engine.py`)
  - **Market Objective**: Screens long-term fundamental compounders for positional allocation and manages active positions during market hours.
  - **Dual-Gate Signal Hierarchy**:
    - **Gate 1 (Bucket Prerequisite)**:
      - *Non-Financial Stocks*: $\text{ROCE} \ge 20.0\%$, $\text{Debt/Equity} \le 1.0$, $\text{YoY Revenue Growth} \ge 10.0\%$.
      - *Financial Services (Banks/NBFCs)*: $\text{ROE} \ge 15.0\%$, $\text{Debt/Equity} \le 3.0$, $\text{YoY Revenue Growth} \ge 10.0\%$.
      - *Extreme Valuation Ceiling*: $\text{PEG} \le 3.0$ (instant kill-gate for extreme bubble valuations).
    - **Gate 2 (Timing Gate)**:
      - Fundamental Quality Score $\ge 55$, Technical Momentum Score $\ge 25$, and $\text{Price} > \text{SMA}_{200}$.
  - **Hybrid 2-Tier Schedule**:
    - *Fast CMP Exit Updates*: Every 5 minutes during market hours (<3.0s runtime) to monitor active portfolio exit stops.
    - *Full BUY Alert Scans*: Every 15 minutes during market hours to evaluate full 287-stock universe for new buy entries.

  ## 2.6 Multibagger Engine (`app/multibagger.py`)
  - **Market Objective**: Evaluates multi-year compounders combining fundamental quality, low promoter pledge ($\le 10\%$), high capital efficiency, and technical momentum.
  - **Unified Conviction Tiers & Alert Triggering**:
    - *🚀 Prime Multibagger*: Composite Score $\ge 75$, Quality $\ge 65$, Valuation $\ge 50$, Trend $\ge 10.0$, and **Piotroski F-Score $\ge 7$** ($₹100,000$ capital allocation). Generates active BUY alert when in buy zone.
    - *💎 High Quality*: Composite Score $\ge 65$, Quality $\ge 60$, Trend $\ge 10.0$ ($₹50,000$ capital allocation). Generates active BUY alert when in buy zone.
    - *🟡 Watchlist*: Composite Score $50–64$. **Non-alerting watchlist tier** (tracked in display cache for fundamental monitoring; strictly blocked from generating active BUY alerts).
  - **Category Label Binding**: The `category` column in the database (`alerts`) and alert payloads is strictly bound to the final post-bonus conviction tier (`tier`), ensuring zero mis-stamping of active alerts.
  - **Execution Schedule**: **04:00 AM IST Cold Start** (initial screening with fresh daily watchlist) + **19:00 PM IST Daily Scan** (post-market full scan) + **15-minute intraday exit monitor**.

  ---

  # 3. TRADE EXECUTION, SIGNAL DELIVERY & ALERT LIFECYCLE

  ## 3.1 Alert Payload Structure
  Every alert generated by the system contains complete structural parameters:
  - **Symbol**: Standard NSE/BSE ticker (e.g. `RELIANCE`, `TATAMOTORS`, `YASHHV.BO`).
  - **Scanner Source**: `EOD`, `MULTI_TF`, `REVERSAL`, `PULLBACK`, `WEALTH`, `MULTIBAGGER`.
  - **Entry Price**: Recommended breakout execution price (in ₹ / RS).
  - **Initial Stop Loss**: Structural stop loss calculated at signal generation time (**Immutable**).
  - **Trailing Stop Loss**: Mutable stop loss updated as targets are hit.
  - **Targets (T1 to T4)**:
    - Dynamically generated using the `ClusterEngine`, which scans for structural resistance nodes (prior swing highs, volume nodes, moving averages, Fibonacci extensions).
    - Targets are chosen in ascending order of resistance intensity rather than arbitrary R-multiples.
  - **Composite Score**: 0–100+ quality score rendered in the **All Trades Table** and signal cards.

  ### 3.1.1 Composite Quality Score & Selection Mechanics
  - **What the Score Represents**: The `score` column displayed in the **All Trades Table** is a **Composite Technical & Fundamental Quality Score (0–100+)** calculated dynamically by the scoring engine (`app/scoring_engine.py`) at signal generation time. It combines:
    1. *Technical Breakout Quality*: Candle body ratio, close position, ATR expansion, 50D-high breakout bonus (+5 pts).
    2. *Volume & Liquidity Conviction*: Volume surge ratio vs 20D Median Volume baseline, NSE delivery percentage, institutional block deals.
    3. *Trend & Momentum Stack*: Moving average alignment ($\text{EMA}_9 > \text{EMA}_{20} > \text{SMA}_{50} > \text{SMA}_{200}$), ADX trend strength, RS Percentile rating vs Nifty 50.
    4. *Fundamental Quality*: Piotroski F-Score ($\ge 7$), sector tailwind bonus (+3 pts), ROCE/ROE efficiency, and PEG valuation ceiling.
    5. *Penalties*: Deductions for extension above EMA20, unsustained volume, young listings ($< 200$ bars), or high promoter pledge ($> 10\%$).

  - **How Scanners Select Alerts Using Score**:
    1. **Hard Minimum Floor (Filtering)**: A candidate is immediately rejected (`rejected["low_score"]`) if `Score < Minimum_Threshold`:
       - EOD Breakout: $\ge 82$ (elevated dynamically in bear regimes)
       - Multi-TF Intraday: $\ge 78$
       - Reversal Scanner: $\ge 62$ (elevated to **$\ge 90$** in `STRONG_BEAR` macro regimes)
       - Pullback Pipeline: $\ge 75$
       - Wealth Engine: $\ge 55$
       - Multibagger Engine: $\ge 65$ (High Quality), $\ge 75$ (Prime Multibagger)

  ### 3.1.2 Earnings & Corporate Event Warning Badging (No Hard-Block Policy)
  - **Policy Statement**: Upcoming earnings announcements or corporate action windows do **NOT** hard-block scanner trade generation. Trades meeting technical and fundamental criteria continue to fire normally.
  - **Automated Metadata Enrichment**: Every generated alert is automatically enriched at database insertion time (`save_alert_if_new`) with earnings metadata from `EarningsCalendarService` (`earnings_flag`, `days_to_earnings`, `earnings_date`, `earnings_severity`, `warning_msg`).
  - **UI Visual Badging**: The User & Admin Dashboards visually badge event risk directly in the **All Trades Table** symbol column and detail panels:
    - `🔴 RESULTS TODAY`: Earnings expected today (0 days).
    - `🟠 RESULTS IN 1D / 2D`: Earnings expected in 1 to 2 days.
    - `🟡 RESULTS IN 3D–5D`: Earnings expected in 3 to 5 days.
    - `⚠️ UNVERIFIED`: Missing or unverified calendar date.
    2. **Descending Rank Selection (Truncation)**: When multiple stocks pass all technical and fundamental filters on the same scan cycle, the scanner sorts candidates in descending order by Score:
       ```python
       approved_candidates.sort(key=lambda x: x["score"], reverse=True)
       ```
       Only the **Top-N highest-scoring candidates** (e.g. Top-10 for EOD/Reversal/Pullback) are selected and persisted into the `alerts` table. Lower-scoring candidates exceeding the limit are suppressed (`RANKED_OUT`).

  ## 3.2 Signal Delivery Channels
  1. **Telegram Channels**: Automated instant posts with symbol, price, stop loss, targets, and chart links.
  2. **Web Push Notifications**: Browser push notifications via VAPID delivered to mobile and desktop browsers.
  3. **In-App Portal Cards**: Live updating signal cards rendered on the User & Admin Dashboards.

  ## 3.3 How to Trade Scanner Alerts
  1. **Entry Execution**:
    - *EOD / Reversal / Pullback Alerts*: Generated after 18:00 IST. Enter position at Next Day Market Open (09:15 AM IST) or via limit order near the alert entry price.
    - *Multi-TF Intraday Alerts*: Triggered intraday. Enter immediately upon receiving alert if price is within 0.5% of recommended entry price.
  2. **Position Sizing & Risk Rules**:
    - Risk fixed capital per trade (e.g., 1% of total portfolio equity).
    - Position Size (Shares) = $\frac{\text{Capital at Risk (₹)}}{\text{Entry Price} - \text{Initial Stop Loss}}$.
  3. **Profit Booking & Exit Profiles (`EXIT_PROFILES`)**:
    - 100% of booked position size is liquidated across **Target 1 (T1)**, **Target 2 (T2)**, and **Target 3 (T3)** based on the scanner's assigned profile:
      - **BALANCED** (EOD & Pullback): Sell 30% at T1, 40% at T2, 30% at T3.
      - **AGGRESSIVE** (Multi-TF): Sell 20% at T1, 30% at T2, 50% at T3 (holds majority for momentum continuation).
      - **CONSERVATIVE** (Reversal): Sell 25% at T1, 50% at T2, 25% at T3.
    - **Target 4 (`target_4`) Status**: `target_4` is an **informational structural runner target** used for analytical quality scoring, notification alerts, and dashboard tracking. Position size is 100% liquidated by T3.
    - **Trailing Stop Loss Rules**:
      - At T1: Trail `stop_loss` to Breakeven (Entry Price).
      - At T2: Trail `stop_loss` to Target 1 (T1).
      - At T3: Trade is fully closed.
    - **Full Exit Overwrite Guard & Terminal Immutability**: When remaining shares $\le 0$ (trade fully closed at T3, SL, or Expiry), the system freezes `status`, `stop_loss`, and `exit_reason` columns to preserve the trade's final historical record.

  ## 3.4 Exit Alert System
  Exits are triggered and notified under five specific conditions:
  1. **Stop Loss Breach (`LOSS`)**: Candle Low drops below active `stop_loss`.
  2. **Target Hit (`WIN` / `PARTIAL_WIN`)**: Price reaches T1, T2, or T3. (Target 4 is an informational runner target).
  3. **Structural Failure Exit**: Price closes below structural support (EMA20 / SMA50) before T1.
  4. **Trailing Stop Exit**: Price reverses after hitting T1/T2 and hits trailed stop.
  5. **Time Expiry (`EXPIRED`)**: Trade fails to hit T1 within 20 trading days (40 days for REVERSAL).

  > [!IMPORTANT]
  > **Intrabar Precedence Rule**: If a single candle touches both Stop Loss (Low <= SL) and Target (High >= T1/T2/T3), **Stop Loss (`LOSS`) takes conservative precedence**.

  ---

  # 4. DASHBOARD SUITE & USER/ADMIN WORKFLOWS

  The platform provides three integrated, real-time glassmorphic web dashboards served autonomously by Flask (`app/dashboard_server.py`) and Jinja2 templates (`app/templates/`):

  ## 4.1 User Dashboard (`/`)
  Designed for active traders and portfolio managers to monitor live market signals, active position lifecycles, and risk-reward dynamics:

  ### Key Components & Panels:
  1. **Real-time Telemetry KPI Cards**:
     - **Active Positions**: Count of currently open trades (`OPEN` / `TRAILING`).
     - **Total Closed Trades**: Total historical trades completed (`WIN`, `PARTIAL_WIN`, `LOSS`, `EXPIRED`).
     - **Win Rate (%)**: Realized win rate across all historical trades.
     - **Net PnL (₹ and %)**: Cumulative realized portfolio profit/loss.
     - **Active Viewers Badge**: Live SSE/Polling connection counter (`/api/viewers`).
     - **Web Push VAPID Toggle**: One-click browser push notification subscription toggle (`/api/push/subscribe`).
  2. **Scanner Category Filter Tabs**:
     - Instant client-side filtering by scanner engine: **ALL**, **EOD Breakout**, **Multi-TF Intraday**, **Reversal**, **Pullback Pipeline**, **Wealth Engine**, **Multibagger Engine**.
  3. **Signal Cards & Active Signals Table**:
     - Displays every alert with real-time price updates (CMP) polled from NSE/BSE data providers:
       - **Symbol**: Standard NSE/BSE ticker linked directly to TradingView charts (`https://in.tradingview.com/chart/?symbol=NSE:{symbol}`).
       - **Scanner Badge**: Color-coded pill (`EOD` blue, `MULTI_TF` purple, `REVERSAL` orange, `PULLBACK` cyan, `WEALTH` green, `MULTIBAGGER` gold).
       - **Entry Price (₹)**: Recommended execution price.
       - **Initial Stop Loss (₹)**: Structural stop loss calculated at signal generation time (**Immutable**).
       - **Trailing Stop Loss (₹)**: Active trailing stop updated as targets are hit.
       - **Target Pillars (T1, T2, T3, T4)**:
         - `Target 1`: Dynamic resistance cluster level (30% liquidation in Balanced mode; trails SL to Breakeven).
         - `Target 2`: Dynamic resistance cluster level (40% liquidation in Balanced mode; trails SL to T1).
         - `Target 3`: Final exit target (30% liquidation; 100% position closed).
         - `Target 4`: **Informational Structural Runner Target** (0% position allocation; tracked for analytical quality scoring).
       - **Natural Risk-Reward ($R:R$)**: $\frac{\text{Target}_1 - \text{Entry}}{\text{Entry} - \text{StopLoss}}$.
       - **Composite Score**: 0–100+ quality score.
       - **Status Badges**: `OPEN` (active), `TRAILING` (T1/T2 hit), `WIN` (T3 hit), `PARTIAL_WIN` (T1/T2 hit then stopped out), `LOSS` (stop loss hit), `EXPIRED` (holding period expiry).
       - **Live PnL (%)**: Real-time gain/loss calculated as $\frac{\text{CMP} - \text{Entry}}{\text{Entry}} \times 100\%$.

  ---

  ## 4.2 Admin Dashboard (`/admin`)
  Designed for system administrators and operations monitoring to manage autonomous scanner execution, memory performance, counterfactual shadow tracking, and notifications:

  ### Key Components & Panels:
  1. **Scanner Health & Control Grid (6 Scanner Cards)**:
     - Real-time status cards for **EOD**, **MULTI_TF**, **REVERSAL**, **PULLBACK**, **WEALTH**, and **MULTIBAGGER**.
     - Displays Status Pill (`OK` green, `RUNNING` blue, `QUEUED` yellow, `DEFERRED` orange, `DEGRADED` purple, `DOWN` red).
     - **Manual "Run Scanner Now" Trigger**: One-click button initiating an asynchronous manual scan cycle (`/api/trigger-scanner`).
     - **Dynamic Sliding Queue**: Renders real-time queue positions (`QUEUED-1`, `QUEUED-2`, etc.) calculated based on request timestamps.
  2. **Counterfactual Shadow Tracking Table & Quality Metrics**:
     - Monitors system-rejected trades (`is_rejected = TRUE`) in the background without affecting live portfolio equity curves.
     - Displays `ghost_symbol`, `original_scanner`, `rejection_reason`, `entry_price`, `shadow_status`, `shadow_exit_price`, `shadow_pnl_pct`:
       - `👻 SHADOW WIN`: Rejection touched Target 1/2/3 before SL (Hypothetical Missed Win).
       - `👻 SHADOW LOSS`: Rejection touched Stop Loss before Target (Hypothetical Correct Rejection).
       - `👻 SHADOW EXPIRED`: Rejection timed out after 40 trading days.
     - **Rejection Quality Metrics**:
       - **True Negatives Rate (%)**: Percentage of rejected trades that ended in `SHADOW_LOSS` (Validates rejection engine quality).
       - **False Negatives Rate (%)**: Percentage of rejected trades that ended in `SHADOW_WIN` (Identifies overly strict filters).
  3. **System Notification Log & Health Telemetry**:
     - Displays API rate-limit throttles, Fyers failover events, Bhavcopy fallback warnings, and database connection pool health.
     - **Mutex Lock Telemetry (`/api/lock-stats`)**: Displays active lock acquisitions, wait times, hold times, and contention events for scanner process locks.
     - **Memory Profiler Timeline**: Stage timeline breakdown and heap usage memory profiling metrics.
  4. **System Action Controls**:
     - **Test Telegram Alert**: Triggers a test payload to configured Telegram channels (`/api/test_telegram`).
     - **Test Push Notification**: Triggers a test push payload to registered Web Push devices (`/api/test_push`).
     - **Export Watchlists / Data**: Direct CSV/JSON export buttons for watchlists, alerts, and outcomes.

  ---

  ## 4.3 Performance Tracker Dashboard (`/performance`)
  Designed for quantitative analytics, equity curve auditing, and multi-scanner strategy comparison:

  ### Key Components & Panels:
  1. **Cumulative Equity Curve & Benchmark Comparison**:
     - Interactive Chart.js visual tracking of cumulative portfolio return (%) over time vs Nifty 50 benchmark index.
  2. **Strategy Key Performance Indicators (KPIs)**:
     - **Cumulative Win Rate (%)**: $\frac{\text{Total Winning Trades}}{\text{Total Closed Trades}} \times 100\%$.
     - **Profit Factor**: $\frac{\text{Gross Realized Profits (₹)}}{\text{Gross Realized Losses (₹)}}$.
     - **Average Win vs Loss Ratio**: Average profit per winning trade divided by average loss per losing trade ($R:R$).
     - **Max System Drawdown (%)**: Peak-to-trough equity decline.
     - **Average Holding Period**: Mean trading days elapsed from entry to exit.
  3. **Monthly Return Heatmap Matrix**:
     - Grid displaying net realized returns (%) broken down by month and year.
  4. **Scanner-by-Scanner Expectancy Matrix**:
     - Detailed performance table breaking down win rate, profit factor, total signals, net PnL, and max drawdown individually across all 6 scanners (`EOD`, `MULTI_TF`, `REVERSAL`, `PULLBACK`, `WEALTH`, `MULTIBAGGER`).
  5. **Historical Trade Audit Log**:
     - Complete, searchable, sortable audit table of every historical trade with full entry/exit parameters, exit signal reason, and timestamp.

  ---

  ---

  # 5. SYSTEM SCHEDULE & OPERATING TIMELINE

  ```
  00:00 IST ── Midnight Rotation
                └─ Reset SessionContext, release daily caches, purge memory (gc.collect())
  01:00 IST ── Daily Builder Run
                └─ Scrape TradingView universe -> Update data/watchlist.parquet
  02:00 IST ── Wealth Engine Initial Sweep
                └─ Pre-market fundamental & momentum scoring for all 287 compounders
  08:30 IST ── Readiness Verification Check
                └─ Verify watchlist freshness, DB schema health, and data readiness
  09:14 IST ── Pre-Market Warmup (09:14:30 IST)
                └─ Pre-fetch 15m/1H price data for Multi-TF scanner to prevent 09:15 tick lag
  09:15 IST ── Market Open (SessionContext -> MARKET_OPEN)
                ├─ Every 5 min:  Wealth Engine CMP Exit Updates + Performance Tracker (<3s)
                └─ Every 15 min: Multi-TF Scanner (:00/:15/:30/:45) + Wealth BUY Scan + Multibagger Exit Monitor
  15:30 IST ── Market Close (SessionContext -> POST_MARKET)
  18:00 IST ── Evening Batch Scanners (Sequential)
                ├─ 1. Poll for NSE Bhavcopy delivery publication (every 5 mins until 20:30 IST fallback)
                ├─ 2. Run EOD Breakout Scanner (max 10m hard timeout)
                ├─ 3. Run Reversal Scanner (max 10m hard timeout)
                └─ 4. Run Pullback Pipeline Scanner (max 10m hard timeout)

  ## 5.1 Standardized Scanner Execution Banners (`[VERSION: SCANNER_LOCK_BANNERS_v1.0]`)
  Every quantitative scanner and background exit monitor emits a prominent, standardized log banner at `INFO` level upon lock acquisition and release:
  - **Start Banner (Lock Acquired)**:
    `********************* Starting <Scanner Name> Scanner at YYYY-MM-DD HH:MM:SS IST *********************`
  - **Completion Banner (Lock Released)**:
    `********************* <Scanner Name> Scanner completed at YYYY-MM-DD HH:MM:SS IST *********************`
  - **Scope**: Applied universally across all 8 scanner processes (`EOD`, `Reversal`, `Multi-TF`, `Wealth Engine`, `Multibagger`, `Pullback`, `Daily Builder`, `MF Breakout Scanner`) and `Exit Monitors / Performance Tracker`.
  - **Memory Profiler Sub-Stage Filtering (`[VERSION: MEMORY_PROFILER_SUBSTAGE_SUPPRESS_v1.0]`)**: Intermediate sub-stage memory profiler snapshots (e.g. `"Wealth: Candidate Selection"`, `"Wealth: Entry Timing"`, `"MTF Price Fetch"`) route to `logger.debug` level to keep production logs clean and un-cluttered.
  ## 4.4 "Analyse Your Watchlist" Diagnostic System & Personal Watchlist (`app/stock_analyzer.py`)
  An on-demand stock analysis, diagnostic, and watchlist management suite accessible on both User and Admin dashboards:
  - **Strict NSE/BSE Master Ticker Validation (`validate_nse_bse_ticker`)**: Integrates `_load_master_symbol_dictionary()` (covering active watchlists, excluded datasets, full `temp_universe.parquet` (940+ tickers), historical price caches (~685 tickers), and Postgres symbol mappings). Tickers are validated via strict 5-stage verification (master dictionary, BSE mappings, DB `symbol_mappings`, Yahoo Search API, and historical price fetcher check), cleanly rejecting non-existent tickers (e.g. `NONEXISTENT999`) with HTTP 400.
  - **100% Parity Across All 7 Scanner Stages via Reusable Production Evaluators**:
    - *Stage 1 (Daily Builder)*: Calls `evaluate_daily_builder_symbol()` in `app/daily_builder.py` validating price floor ($\ge ₹100.0$), turnover ($\ge ₹1.0\text{ Cr}$), bar history ($\ge 50$), promoter blacklist, D/E ceiling, and OPM limits.
    - *Stage 2 (EOD Breakout)*: Calls `evaluate_eod_symbol()` in `app/eod_scanner.py` with real macro regime context, validating 20D high breakout, volume surge ($\ge 1.8\text{x}$ 20D median), upper wick ($\le 35\%$), body ratio ($\ge 45\%$), close position ($\ge 65\%$), ATR expansion ($\ge 0.9$), EMA20 extension ($\le 1.5$ ATR), and bullish candle.
    - *Stage 3 (Multi-TF Intraday)*: Calls `evaluate_multi_tf_symbol()` in `app/multi_tf_scanner.py` independently fetching 1H, 30m, 15m, and 5m intraday datasets via `fetch_watchlist_data()`, evaluating Phase A 1H trend permission ($EMA9 > EMA20 > SMA50$, $Close > SMA200$, $ADX \ge 20$), Phase B 30m base/consolidation ($BB Width Pctile < 0.45$), Phase C 15m micro-alignment ($Close \ge 15m EMA15$), and Phase D 5m trigger/thrust ($Close \ge Resistance$ & $5m Vol \ge 1.2x$). Returns explicit status tags (`CORE MET (Phase A+B+C+D Trigger Ready)`).
    - *Stage 4 (Reversal Oversold)*: Calls `evaluate_reversal_symbol()` in `app/reversal_scanner.py` evaluating mean-reversion oversold bounce ($20\%-45\%$ 52W High drop band, $\text{RSI}(14) \le 38$ or RSI curl $\ge 50$, $\text{Close} > \text{SMA}_{50}$ reclaim).
    - *Stage 5 (Pullback Continuation)*: Calls `evaluate_pullback_symbol()` in `app/pullback_pipeline.py` executing full `swing_utils` swing pivot detection (`detect_confirmed_pivots`), impulse origin selection (`select_pullback_origin` with $\ge 8\%$ gain), retracement depth ($23.6\%-61.8\%$) & volume contraction measurement (`measure_pullback`), and resumption trigger bar validation (`detect_resumption_trigger`), guaranteeing 100% parity with `pullback_pipeline.py`.
    - *Stage 6 (Wealth Engine)*: Calls `evaluate_wealth_symbol()` in `app/wealth_engine.py` evaluating all 4 fundamental buckets matching `wealth_engine.py` 100% (Core Compounder: $\text{ROCE} \ge 20\%$, $\text{ROE} \ge 15\%$, $\text{D/E} \le 0.50$; Growth Multiplier: YoY Revenue $\ge 20\%$, YoY Profit $\ge 20\%$; Quality-On-Sale: $\text{ROCE} \ge 15\%$, $\text{D/E} \le 1.0$, Drop from 52W High $\ge 15\%$; Opportunistic: YoY Profit $\ge 40\%$) coupled with mandatory CMP > SMA200 technical trend gate and PEG $\le 3.0$ valuation ceiling.
    - *Stage 7 (Multibagger Engine)*: Calls `evaluate_multibagger_symbol()` in `app/multibagger.py` evaluating 2 conviction tiers matching `multibagger.py` 100%: `🚀 Prime Multibagger` ($\text{Piotroski} \ge 7$, $\text{Pledge} \le 10\%$, Uptrend) and `💎 High Quality Multibagger` ($\text{Composite Score} \ge 65.0$, $\text{Pledge} \le 15\%$, Uptrend), with strict non-null handling for pledge and Piotroski data.
  - **Bulk Watchlist Vectorization (`analyze_watchlist`)**: Bulk batch processor in `app/stock_analyzer.py` executing 1-pass vectorized market data fetching across multi-stock watchlists. Replaces $N \times 6$ sequential API round-trips with 1 bulk download pass, reducing deep analysis latency for 10-stock watchlists by over 80%.
  - **Fundamental Dict Ratio & Multi-Alias Synchronization**: Automatically extracts `roce`, `roe`, `debt_equity`, `yoy_revenue`, `yoy_profit`, and `promoter_pledge_pct` from `watchlist_cache`, `temp_universe.parquet`, and PostgreSQL `promoter_pledge_cache`, syncing them into `fund_data` across all three alias formats (`snake_case`, `Title Case`, and `%` suffix) prior to evaluator calls.
  - **Rich Diagnostic Execution Logging**: Emits structured logger events (`logger.info` / `logger.debug`) during single-stock and bulk analysis runs detailing exact stock processing steps, active scanner evaluators, fundamental ratio resolution parameters, composite health scores, and qualified scanner outputs.
  - **Inline Main Screen Diagnostic & Watchlist Layout**: Renders both the 7-stage scanner diagnostic panel (`#stock-diagnostic-main-container`) and personal monitored watchlist (`#my-watchlist-section`) directly inline on the main screen of `admin_dashboard.html` and `user_dashboard.html` right below the search widget rather than in overflowing modal popups, with smooth auto-scroll into view and 1-click collapse buttons.
  - **Explicit Watchlist Click & Toggle**: Stocks are strictly added to personal watchlists only when the user explicitly clicks `⭐ Add to Watchlist` (eliminating false silent auto-add assumptions on search). Clicking `✓ Added to Watchlist` toggles removal cleanly.
  - **Repositioned Advanced Outcome Analytics (PREVIEW)**: Placed directly below the Scanner Health grid (`#scannerGrid`) inside System Health & Diagnostics for logical workflow flow.
  - **Piotroski Score Preservation & Safe On-Demand Merging**: Merging on-demand fundamental fetch results (`fund_data.update`) preserves any existing valid Piotroski score ($\ge 0$) in cache, preventing live daily Yahoo fetches from overwriting full annual Piotroski ratings.
  - **Strict Per-User Watchlist Isolation & Real-Time Dynamic Evaluation (`user_watchlists`)**: Safely handles integer vs string user IDs (e.g. `DEFAULT_USER`, `admin`, `57880`) across session queries and database functions using strict per-user filtering (`WHERE user_id::text = %s`). Evaluates `is_in_watchlist` dynamically against the requesting user's live database entries in real-time, overriding frozen master cache fields (`stock_analysis_master`) and client RAM caches (`WATCHLIST_ITEMS_CACHE`) to guarantee 100% data privacy and isolation so each user only views and manages their own personal watchlist.
  - **Universal Multi-Source Autocomplete Search**: Subsecond NSE/BSE ticker suggestions (`/api/v1/symbols/suggest?q=PREFIX`) querying a master registry across active watchlists, excluded datasets, full universe (`temp_universe.parquet`), historical price caches (~685 tickers), and Postgres symbol mappings. Includes a dynamic fallback (`Select 'TICKER' (NSE/BSE)`) guaranteeing coverage for **all ~4,000+ listed NSE & BSE stocks**.
  - **Overall Health Score (0-100)**: Quantitative composite score combining Technical Trend (50%), Fundamental Quality (30%), and RS Percentile vs Nifty 500 (20%).
  - **"What It Lacks" Deficit Summary**: Bulleted list of parameter gaps holding a stock back from becoming a top-tier active alert (e.g. volume ratio deficit, upper wick excess, Piotroski F-score gap, leverage).
  - **7-Stage Scanner Funnel Table & Full Quantitative Gate Reasons**: Fast-path evaluation through all 6 scanners. Displays full quantitative gate criteria details (Close price, Volume surge ratio, RSI value, SMA50/200 alignment, ROC) alongside status badges (`⚡ CORE MET` in Orange `#f59e0b`, `✓ QUALIFIED` in Green `#10b981`, `🔍 MONITORING` in Blue `#3b82f6`). Displays operational taxonomy flags (`setup_qualified`, `production_eligible`, `selected_for_alert`).
  - **Deep Analysis & Zero-Fallback Evaluator Qualification for Alert Creation**: Manual alert creation (`🚀 Raise Alert`) requires `is_deep_analysis = True` and enforces strict boolean qualification contract (`scanner_stage.get("qualified") is True`). Requires canonical risk package (`entry_price`, `stop_loss`, `target_1`, `score`) and conviction tier without artificial fallback calculations, rejecting incomplete evaluator returns with HTTP 400. Saves verbatim evaluator $T_4$, $RS$, sector, and regime metadata to database `alerts`.
  - **Persistent Deep Analysis & Outcome JSON Storage**: Complete 7-stage diagnostic analysis outcomes are persisted in `user_watchlists` (`deep_analysis_result` JSONB & `last_deep_analysis_at` TIMESTAMPTZ). When a stock is fetched in the watchlist, its last analysis timestamp (in IST) and saved diagnostic breakdown are loaded instantly. Re-scanning overwrites and updates the database with fresh timestamps and new diagnostic metrics.
  - **Master Symbols Database Registry & Admin Manual Sync (`/api/v1/admin/master_symbols/refresh`)**: Pre-populates all active listed NSE & BSE equities into PostgreSQL `master_symbols` table. Features a daily 07:00 AM IST scheduled refresh job as well as an Admin Dashboard action button (`⚡ Sync Master Stock List`), allowing administrators to manually update the universe of all listed stocks anytime of day.
  - **IST Timestamp Standardization**: All added dates and last scanned timestamps are explicitly formatted and displayed in **Indian Standard Time (IST - Asia/Kolkata)** across tables and modals.
  - **Watchlist Badge Synchronization**: Watchlist item counts are synchronized in real-time across main UI header buttons (`#my-watchlist-count`) and modal titles (`#modal-watchlist-count`).
  - **Glassmorphic Modals, Body Scroll Locking & Standardized Confirm Popups**:
    - Implemented `document.body.style.overflow = 'hidden'` on modal open and `overscroll-behavior: contain` on modal containers across `#stock-diagnostic-modal`, `#my-watchlist-section`, and `#custom-confirm-modal`, preventing background page scrolling when interacting with modals.
    - Replaced browser default `confirm()` popups with custom dark glassmorphism confirmation cards (`showCustomConfirmModal`) matching standard validation alerts.
    - Features explicit `event.stopPropagation()` on close buttons (`✕`), z-index isolation (`z-index: 9999-10010`), backdrop click-to-close overlay, and `Esc` keyboard shortcut listener.
  - **Session Stability & Permanent Authentication**: Configured `_SESSION_CACHE_TTL = 300s` (5 minutes) and a static production `SECRET_KEY` fallback (`ELITE_BREAKOUT_SYSTEM_SECURE_PERMANENT_SECRET_KEY_PROD_2026_V10`) with `PERMANENT_SESSION_LIFETIME = timedelta(days=30)` to eliminate unexpected user logouts during Railway restarts and parallel API calls.
  - **Ticker Aliasing & Symbol Resolution Pipeline**: Automatically maps reorganized or renamed tickers (e.g. `TATAMOTORS` -> `TMCV.NS`) in data providers and price fetchers (`data_provider.py`), guaranteeing seamless historical price downloading and 7-stage diagnostic analysis execution without delisting 404 errors.
  - **Sub-Millisecond 0ms Client-Side Autocomplete Engine**: Pre-loads all 2,389+ official NSE equities from `/api/v1/symbols/master_list` into browser RAM on page load (`window.MASTER_SYMBOLS_CLIENT_ARRAY`), performing instant client-side autocomplete searches in `<0.1ms` without network keystroke lag.
  - **Personal Monitored Watchlist**: Save non-qualifying or monitored stocks with `[ ⭐️ Add to Watchlist ]` into database table `user_watchlists`. Features a `[ 🔄 Re-Scan ]` button on dashboard tables for instant re-evaluation.

  ---
  *End of System Specification & User/Admin Guide — `docs/SYSTEM_SPECIFICATION.md`*
