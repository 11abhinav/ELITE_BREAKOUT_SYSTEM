  # ELITE BREAKOUT SYSTEM — SYSTEM SPECIFICATION & USER/ADMIN GUIDE

  > **Document Class:** User & Admin Operational Manual
  > **Status:** Canonical Master Guide for system functionality, trading rules, and dashboard operations.
  > **Target File:** `docs/SYSTEM_SPECIFICATION.md`
  > **Last Synchronized:** 2026-07-25 (v8.4.3+)

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
  - **Tier Classification**:
    - *🚀 Prime Multibagger*: Composite Score $\ge 75$, Quality $\ge 65$, Valuation $\ge 50$, Trend $\ge 10.0$, and **Piotroski F-Score $\ge 7$**.
    - *💎 High Quality*: Composite Score $\ge 65$, Quality $\ge 60$, Trend $\ge 10.0$.
  - **Exit Monitor**: Runs every 15 minutes during market hours to track trailing stops and fundamental decay signals.

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
  - **Risk-Reward Ratio ($R:R$)**: $\frac{\text{Target}_1 - \text{Entry}}{\text{Entry} - \text{StopLoss}}$. The `TradeStructureValidator` ensures the natural structural R:R ratio derived from the closest valid resistance cluster is acceptable.
  - **Composite Score**: 0–100+ quality score (can theoretically exceed 100 with massive bonus multipliers).

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

  The platform provides three integrated web interfaces served by Flask (`dashboard_server.py`):

  ## 4.1 User Dashboard (`/`)
  Designed for active traders and investors monitoring live market signals:
  - **Live Alerts Feed**: Real-time updating grid of trade cards displaying Symbol, Scanner Name, Entry, Stop Loss, Targets, Score, and Age.
  - **Active Signals Table**: Interactive table showing current trade status (`OPEN`, `TRAILING`, `WIN`, `LOSS`), active trailing stops, and percentage gain/loss in ₹ RS.
  - **TradingView Integration**: Clickable ticker links opening TradingView charts (`target="_blank" rel="noopener noreferrer"`).
  - **Filtering & Search**: Instant filtering by scanner type (`EOD`, `MULTI_TF`, `REVERSAL`, `PULLBACK`, `WEALTH`), status, and symbol search.

  ## 4.2 Admin Dashboard (`/admin`)
  Designed for system administrators and operations monitoring:
  - **System Health Panel**: Real-time status cards for all 6 scanners (`OK`, `RUNNING`, `QUEUED`, `DEFERRED`, `DEGRADED`, `DOWN`, `IDLE`), last run timestamp, today's alert count, and execution duration in seconds.
  - **Manual Scanner Triggers**: Interactive buttons to trigger manual on-demand scanner sweeps (`/api/trigger-scanner`).
  - **Dynamic Queue Management**: Displays queue positions (`QUEUED-1`, `QUEUED-2`, etc.) calculated dynamically based on request timestamps.
  - **Mutex Lock Telemetry**: Real-time telemetry on lock acquisitions, wait times, hold times, and contention events (`/api/lock-stats`).
  - **Notification Board**: System alerts for API rate-limit throttles, Fyers failover warnings, and Bhavcopy fallback notifications.

  ## 4.3 Performance Tracker Dashboard (`/performance`)
  Designed for quantitative analytics and performance auditing:
  - **Equity Curve & Cumulative PnL**: Interactive visual tracking of net system returns.
  - **Win Rate & Metrics KPIs**: Win rate %, Average R:R, Max Drawdown %, Average Holding Period.
  - **Historical Trade Log**: Complete searchable audit log of every historical signal and exit reason.

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
  19:00 IST ── Multibagger Daily Scanner Run
  ```

  ---
  *End of System Specification & User/Admin Guide — `docs/SYSTEM_SPECIFICATION.md`*
