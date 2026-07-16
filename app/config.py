# =====================================================================================
# app/config.py
# Centralized configuration for all scanners
# =====================================================================================

import os

# =====================================================================================
# BASE DIRECTORY
# =====================================================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

# =====================================================================================
# TELEGRAM CONFIG (DYNAMIC ENVIRONMENT READ)
# =====================================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = os.getenv("CHAT_ID")

_thread_eod      = os.getenv("THREAD_EOD")
_thread_intraday = os.getenv("THREAD_INTRADAY")
_thread_1h       = os.getenv("THREAD_1H")
_thread_reversal = os.getenv("THREAD_REVERSAL")

THREAD_EOD      = int(_thread_eod)      if _thread_eod      else None
THREAD_INTRADAY = int(_thread_intraday) if _thread_intraday else None
THREAD_1H       = int(_thread_1h)       if _thread_1h       else None
THREAD_REVERSAL = int(_thread_reversal) if _thread_reversal else None

# =====================================================================================
# DATA DIRECTORY & PATHS
# =====================================================================================

DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

WATCHLIST_PATH = os.path.join(DATA_DIR, "elite_fundamental_watchlist.parquet")
DB_PATH = os.path.join(DATA_DIR, "alerts.db")

# =====================================================================================
# SCORE THRESHOLDS & AI
# =====================================================================================

ENABLE_AI_SENTIMENT_SCORE = True  # Set False to disable experimental AI sentiment scoring for audit/backtest runs

SCORE_THRESHOLDS = {
    "15m": 78,
    "1h":  80,
    "1d":  82,
}

# =====================================================================================
# SCAN CONFIGURATION (Algorithm Parameters)
# =====================================================================================
ACTIVE_ALGO_VERSION = "SL_ENGINE_V6.4"  # Updated: MarketRegime + StrategyPolicy + TradeRankingEngine + PortfolioEngine + OpportunityManager


INTRADAY_CONFIG = {
    "MIN_SIGNALS":        2,
    "MIN_BODY_RATIO":     0.60,
    "MIN_CLOSE_POSITION": 0.70,
    "MAX_UPPER_WICK":     0.20,
    "MIN_VOLUME_RATIO":   2.5,
    "MIN_VOLUME_AVG":     150_000,
    "MIN_RSI":            52,
    "MAX_RSI":            87,
}

LIVE_1H_CONFIG = {
    "MIN_SIGNALS":        3,
    "MIN_BODY_RATIO":     0.55,
    "MIN_CLOSE_POSITION": 0.65,
    "MAX_UPPER_WICK":     0.25,
    "MIN_VOLUME_RATIO":   2.0,
    "MIN_VOLUME_AVG":     100_000,
    "MIN_RSI":            55,
    "MAX_RSI":            86,
}

EOD_CONFIG = {
    "MIN_SIGNALS":        1,
    "MIN_BODY_RATIO":     0.45,
    "MIN_CLOSE_POSITION": 0.65,
    "MAX_UPPER_WICK":     0.35,
    "MIN_VOLUME_RATIO":   1.8,
    "MIN_VOLUME_AVG":     50_000,
    "MIN_RSI":            55,
    "MAX_RSI":            88,
}

EOD_ADVANCED_CONFIG = {
    "MAX_DISTANCE_FROM_52W_HIGH_PCT": 15.0,
    "MAX_SINGLE_DAY_MOVE_PCT": 15.0,
    "MAX_GAP_FROM_PRIOR_HIGH_PCT": 3.0,
    "GAP_LOOKBACK_BARS": 10,
    
    # ── Sustainability & Breakout Conviction ──
    "MAX_EXTENDED_BREAKOUT_ATR_MULT": 1.5,
    "MIN_ATR_EXPANSION_RATIO": 0.9,  # [FIX P1] Relaxed from 1.2 — 1.2 rejected steady uptrend breakouts
    "MIN_OBV_SLOPE": 0.0,
    
    # ── Prior Context & Tight Bases ──
    "PRE_BREAKOUT_LOOKBACK_BARS": 5,
    "MAX_PRE_BREAKOUT_RED_CANDLES": 2,
    "TIGHT_BASE_BB_WIDTH_PCTILE": 0.35,
    
    # ── [FIX] Structural Breakout Constraint Relaxation ──
    # Previously 0.20, which contradicted the fact that Bollinger Bands expand upon breakout.
    "MAX_BB_WIDTH_PCTILE": 0.80
}

REVERSAL_CONFIG = {
    "MIN_DROP_FROM_52W_HIGH": 20.0,
    "MAX_DROP_FROM_52W_HIGH": 45.0,
    # ── [FIX] Reversal RSI Constraint Relaxation ──
    # Since above_sma50 is a strict gate, the stock is recovering. Thus RSI won't be deeply oversold (<35) recently.
    "RSI_OVERSOLD_THRESHOLD": 45,
    "RSI_CURL_MIN": 50,
    "MIN_VOLUME_RATIO": 2.0,
    "MIN_AVG_DAILY_VOLUME": 300_000,
    "MIN_ROE": 12.0,
    "MIN_YOY_REVENUE_GROWTH": 8.0,
    "MAX_DROP_BELOW_SMA200": 25.0,
    "REVERSAL_COOLDOWN_TRADING_DAYS": 30
}

ALERT_COOLDOWN_MINUTES = {
    "EOD": 390,
    "REVERSAL": 240,
    "LIVE": 240,
    "INTRADAY": 90
}


# [FINDING-F FIX] Lowered ADX from 25 to 18. ADX 25+ indicates a trend that has
# already moved significantly. ADX 18-24 captures the accumulation/developing phase
# exactly where breakouts occur, while still filtering out choppy (ADX < 18) stocks.
ADX_MIN_THRESHOLD = 18
MIN_STOCK_PRICE = 100.0    # No penny stocks — matches daily_builder MIN_PRICE

# LIQUIDITY THRESHOLDS (in Rupees)
MIN_DAILY_LIQUIDITY_RUPEES_WATCHLIST = 150_000_000  # ₹15 Cr/day for raw watchlist
MIN_DAILY_LIQUIDITY_RUPEES_WEALTH    = 10_000_000   # ₹1 Cr/day for long-term wealth engine

DELIVERY_CONVICTION_THRESHOLDS = {
    "institutional": 60,
    "positional":    40,
    "moderate":      25,
    "intraday_churn": 0,
}

BATCH_DOWNLOAD_SIZE = 30
YAHOO_TIMEOUT = 30
PRICE_CACHE_TTL_SECONDS = 60  # Changed from 180s: Intraday runs every 5min (need fresh cache hit)


TELEGRAM_CHUNK_SIZE = 10
TELEGRAM_RETRIES = 3
TELEGRAM_TIMEOUT = 10
LOG_LEVEL = "INFO"

# =====================================================================================
# ANTI-FAKE-BREAKOUT PARAMETERS
# =====================================================================================

# Minimum % above prior high for a valid breakout (timeframe-aware)
MIN_BREAKOUT_MARGIN = {
    "15m": 0.003,   # 0.3% above prior high
    "1h":  0.005,   # 0.5%
    "1d":  0.007,   # 0.7%
}

# Breakout candle volume must be at least this multiple of 20-bar avg
MIN_BREAKOUT_VOLUME_RATIO = 1.5

# Reject if N prior candles are ALL bearish (no momentum build-up)
# Moved to EOD_ADVANCED_CONFIG["MAX_PRE_BREAKOUT_RED_CANDLES"]

# BASE_WIDTH below this = tight consolidation = bonus-worthy setup
BASE_TIGHTNESS_THRESHOLD = 1.5

# BASE_WIDTH above this = volatile/choppy = penalize
BASE_VOLATILITY_THRESHOLD = 3.0

# =====================================================================================
# ANTI-OPERATOR-TRAP PARAMETERS
# =====================================================================================

# Bars to look back for climax top volume pattern
CLIMAX_VOLUME_LOOKBACK = 20

# Bars to look back for lower-high pattern (failed breakout retest)
LOWER_HIGH_LOOKBACK = 6

# Minimum candle range as % of price (below this = thin spread trap)
MIN_CANDLE_RANGE_PCT = 0.003   # 0.3%

# =====================================================================================
# SL/TARGET ATR CAPS (max target distance from entry, per timeframe)
# =====================================================================================

ADAPTIVE_TARGET_CAPS = {
    "BULL":    {"15m": 8.0, "1h": 10.0, "1d": 12.0},
    "BEAR":    {"15m": 4.0, "1h": 6.0,  "1d": 8.0},
    "NEUTRAL": {"15m": 6.0, "1h": 8.0,  "1d": 10.0}
}

# =====================================================================================
# V6.0 INSTITUTIONAL CONFIGURATION
# =====================================================================================

MIN_NATURAL_RR = {
    "INTRADAY": 1.5,
    "LIVE_1H": 2.0,
    "MULTI_TF": 1.5,
    "EOD": 2.5,
    "REVERSAL": 3.0,
}

MIN_REWARD_POTENTIAL = {
    "MULTI_TF": 1.8,
    "EOD": 4.0,
    "REVERSAL": 8.0,
}

MIN_STOP_PCT = {
    "MULTI_TF": 0.6,
    "EOD": 1.5,
    "REVERSAL": 2.0,
}


MIN_REWARD_POTENTIAL = {
    "INTRADAY": 1.5,
    "LIVE_1H":  3.0,
    "EOD":      5.0,
    "REVERSAL": 4.0
}

TARGET_QUALITY_THRESHOLD = {
    "INTRADAY": 45,
    "LIVE_1H":  50,
    "EOD":      55,
    "REVERSAL": 50
}

# [T1%, T2%, T3%]
PARTIAL_EXIT = {
    "INTRADAY": [70, 30, 0],
    "LIVE_1H":  [50, 30, 20],
    "EOD":      [40, 30, 30],
    "REVERSAL": [30, 30, 40]
}

STRUCTURAL_RESISTANCE_SCORES = {
    "1H Swing High": 35,
    "30m Swing High": 30,
    "15m Swing High": 25,
    "Major Swing High": 40,
    "Swing High": 30,
    "Rolling Swing High": 20,
    "5m Swing High": 20,
    "R2": 20,
    "R1": 15,
}

STRUCTURAL_STOP = {
    "MAX_CLUSTER_WIDTH_ATR": 1.5,
    "DISASTER_BUFFER_PCT": 1.5,
    "SCORES": {
        "1H Swing Low": 35,
        "30m Swing Low": 30,
        "15m Swing Low": 25,
        "Swing Low Cluster": 40,
        "Swing Low": 30,
        "Rolling Swing Low": 25,
        "S1 (Discovery)": 20,
        "S1": 20,
        "SMA200": 30,
        "EMA20": 15,
        "SMA50": 15,
        "VWAP": 15,
        "Intraday Candle Low": 20
    },
    "BONUS_OVERLAP": 15,
    "USE_SUPPORT_CLUSTER": True
}

# =====================================================================================
# FALLBACK PRICE PROVIDER (when YFinance rate-limited)
# =====================================================================================

# ── DATA PROVIDER SETTINGS ──────────────────────────────────────────────────────────
DATA_PROVIDER = os.getenv("DATA_PROVIDER", "auto")  # auto, yfinance, fyers, or kite

# ── FYERS CONFIGURATION ──────────────────────────────────────────────────────────
FYERS_CLIENT_ID = os.getenv("FYERS_CLIENT_ID")
FYERS_SECRET_KEY = os.getenv("FYERS_SECRET_KEY")
FYERS_REDIRECT_URL = os.getenv("FYERS_REDIRECT_URL", "https://elitebreakoutsystem-production.up.railway.app/fyers/callback")
FYERS_TOKEN_PATH = os.path.join(DATA_DIR, "fyers_token.txt")


REGIME_POLICIES = {
    "STRONG_BULL": {
        "allow_breakouts": True,
        "allow_mean_reversion": False,
        "max_new_positions_per_day": 5,
        "min_target_quality_override": 60,
        "min_reward_potential_mult": 1.5,
        "capital_allocation_mult": 1.0
    },
    "WEAK_BULL": {
        "allow_breakouts": True,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 3,
        "min_target_quality_override": 65,
        "min_reward_potential_mult": 1.0,
        "capital_allocation_mult": 1.0
    },
    "RANGEBOUND": {
        "allow_breakouts": False,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 2,
        "min_target_quality_override": 75,
        "min_reward_potential_mult": 0.8,
        "capital_allocation_mult": 0.5
    },
    "WEAK_BEAR": {
        "allow_breakouts": False,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 1,
        "min_target_quality_override": 80,
        "min_reward_potential_mult": 0.8,
        "capital_allocation_mult": 0.5
    },
    "STRONG_BEAR": {
        "allow_breakouts": False,
        "allow_mean_reversion": False,
        "max_new_positions_per_day": 0,
        "min_target_quality_override": 100,
        "min_reward_potential_mult": 0.5,
        "capital_allocation_mult": 0.0
    },
    "NEUTRAL": {
        "allow_breakouts": True,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 3,
        "min_target_quality_override": 65,
        "min_reward_potential_mult": 1.0,
        "capital_allocation_mult": 1.0
    }
}
