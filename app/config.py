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
_thread_multi_tf = os.getenv("THREAD_MULTI_TF")
_thread_1h       = os.getenv("THREAD_1H")
_thread_reversal = os.getenv("THREAD_REVERSAL")

THREAD_EOD      = int(_thread_eod)      if _thread_eod      else None
THREAD_MULTI_TF = int(_thread_multi_tf) if _thread_multi_tf else None
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
# SYSTEM & PROFILING CONFIGURATION
# =====================================================================================

MEMORY_PROFILER_CONFIG = {
    "DEEP_DIAGNOSTIC_RSS_MB": 5.0,
    "MIN_DF_DELTA_MB": 1.0,
    "MAX_TRACEMALLOC_PEAK_MB": 20.0,
    "CONSECUTIVE_TRIGGER_COUNT": 3,
    "RATE_LIMIT_MINUTES": 30
}

# =====================================================================================
# API / FETCH CONFIGURATION
# =====================================================================================

DISABLE_NSE_SURVEILLANCE_FETCH = False  # Set to True in validation environments to avoid WAF/tarpit timeouts

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
ACTIVE_ALGO_VERSION = "SL_ENGINE_V7.1"  # Updated: Target Engine v7 Pipeline, Institutional S/R Clustering, Parallel Orchestration + Combined Audit Fixes

# =====================================================================================
# MOMENTUM BONUS CONSTANTS & RULE 10 RATIONALE
# =====================================================================================
# RS_BONUS (10 pts): Awarded if stock's 63-day RS rating is >= 80th percentile vs Nifty 50 over active scan universe.
# SECTOR_BONUS (8 pts): Awarded if stock belongs to a Top-3 RS sector holding 3-session hysteresis.
# MAX_MOMENTUM_BONUS (15 pts): Hard cap on combined momentum bonuses so RS (+10) and Sector (+8) co-exist (10+5=15) without clipping Sector to zero.
RS_BONUS = 10
SECTOR_BONUS = 8
MAX_MOMENTUM_BONUS = 15



MULTI_TF_CONFIG = {
    "MIN_SIGNALS":        2,
    "MIN_BODY_RATIO":     0.60,
    "MIN_CLOSE_POSITION": 0.70,
    "MAX_UPPER_WICK":     0.20,
    "MIN_VOLUME_RATIO":   2.5,
    "MIN_VOLUME_AVG":     150_000,
    "MIN_RSI":            52,
    "MAX_RSI":            87,
    "PULLBACK_TRIGGER_MODE": "PREVIOUS_HIGH", # Alternatives: PREVIOUS_OPEN, INSIDE_BAR, ENGULFING
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
    "GAP_AND_GO_PENALTY_MULT": 10,
    "GAP_AND_GO_MAX_PENALTY": 20,
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
    "EOD": 5760,       # 96 hours (Prevents duplicate alert generation on market holidays via fallback data)
    "REVERSAL": 5760,  # 96 hours
    "PULLBACK": 1440   # 24 hours
}

# =====================================================================================
# POSITION SIZING & RISK BUDGETING CONFIGURATION
# =====================================================================================
MAX_SL_DISTANCE_PCT = 8.0         # Max allowed stop loss distance % from entry
ACCOUNT_RISK_BUDGET_PCT = 1.0     # Max portfolio equity risk % per trade (Kelly / risk budget)

PULLBACK_CONFIG = {
    "VERSION": "pb-1.0.0",
    "LOOKBACK": 10, "CONFIRM": 3,
    "MIN_IMPULSE_GAIN_PCT": 8.0, "MIN_IMPULSE_ATR": 3.0, "MAX_IMPULSE_BARS": 20,
    "MIN_DEPTH_PCT": 5.0, "MAX_DEPTH_PCT": 15.0,
    "MIN_DURATION": 3, "MAX_DURATION": 20,
    "MAX_INTERNAL_SWINGS": 2, "MAX_PB_VOLUME_RATIO": 0.75,
    "TRIGGER_VOL_MULT": 1.3, "MIN_CLOSE_LOCATION": 0.75,
    "MIN_BODY_ATR": 0.5, "MAX_UPPER_WICK": 0.25, "MAX_ENTRY_GAP_PCT": 3.0,
    "MAX_BONUS": 5, "PRIOR_WINDOW": 30,
    "OUTAGE_THRESHOLD_BUMP": 3,
    "MIN_HISTORY": 260,
    "MODE": "LIVE", "DEBUG_SWINGS": False,
}

# ── Data Quality Framework (V8.0) ──
QUALITY_VALIDATOR_VERSION = "V8.0"

QUALITY_SCORE_WEIGHTS = {
    "row_completeness": 40,
    "missing": 20,
    "price_sanity": 20,
    "continuity": 10,
    "freshness": 10,
}


# Configurable Score Bands for Advanced Outcome Analytics (Feature F-13)
SCORE_BANDS = [
    (70, 75),
    (75, 80),
    (80, 85),
    (85, 90),
    (90, 101),
]


# Maximum percentage of row loss accepted before logging a regression warning
MAX_HISTORY_SHRINK = 0.30


# Source reliability multipliers (0.0 to 1.0). Used for fallback evaluation.
SOURCE_RELIABILITY = {
    "NSE": 1.0,
    "Fyers": 1.0,
    "Cache": 0.95,
    "BSE": 0.70
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
    "MULTI_TF": 1.5,
    "EOD": 2.5,
    "REVERSAL": 2.0,
}

MAX_REASONABLE_RR = {
    "MULTI_TF": 6.0,
    "EOD": 8.0,
    "REVERSAL": 4.0,
}

MIN_TARGET_CONFIDENCE = 40
TARGET_CONFIDENCE_BASELINE = {
    "version": "2026_Q3",
    "percentile": 95,
    "sample_size": 18000,
    "value": 85
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



TARGET_QUALITY_THRESHOLD = {
    "EOD":      55,
    "REVERSAL": 50
}

# [T1%, T2%, T3%]
PARTIAL_EXIT = {
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
FYERS_REDIRECT_URL = os.getenv("FYERS_REDIRECT_URL", "https://elitebreakoutsystem-production-4ad2.up.railway.app/fyers/callback")
FYERS_TOKEN_PATH = os.path.join(DATA_DIR, "fyers_token.txt")


REGIME_POLICIES = {
    "STRONG_BULL": {
        "score_modifier": 0,
        "allow_mean_reversion": False,
        "max_new_positions_per_day": 5,
        "min_target_quality_override": 60,
        "min_reward_potential_mult": 1.5,
        "capital_allocation_mult": 1.0
    },
    "WEAK_BULL": {
        "score_modifier": 0,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 3,
        "min_target_quality_override": 65,
        "min_reward_potential_mult": 1.0,
        "capital_allocation_mult": 1.0
    },
    
    "BULL": {
        "score_modifier": 0,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 3,
        "min_target_quality_override": 65,
        "min_reward_potential_mult": 1.0,
        "capital_allocation_mult": 1.0
    },
    "BEAR": {
        "score_modifier": 5,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 1,
        "min_target_quality_override": 80,
        "min_reward_potential_mult": 0.8,
        "capital_allocation_mult": 0.5
    },
    "SIDEWAYS": {
        "score_modifier": 8,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 2,
        "min_target_quality_override": 75,
        "min_reward_potential_mult": 0.8,
        "capital_allocation_mult": 0.5
    },
    "RANGEBOUND": {
        "score_modifier": 8,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 2,
        "min_target_quality_override": 75,
        "min_reward_potential_mult": 0.8,
        "capital_allocation_mult": 0.5
    },
    "WEAK_BEAR": {
        "score_modifier": 10,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 1,
        "min_target_quality_override": 80,
        "min_reward_potential_mult": 0.8,
        "capital_allocation_mult": 0.5
    },
    "STRONG_BEAR": {
        "score_modifier": 10,
        "allow_mean_reversion": False,
        "max_new_positions_per_day": 0,
        "min_target_quality_override": 100,
        "min_reward_potential_mult": 0.5,
        "capital_allocation_mult": 0.0
    },
    "NEUTRAL": {
        "score_modifier": 0,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 3,
        "min_target_quality_override": 65,
        "min_reward_potential_mult": 1.0,
        "capital_allocation_mult": 1.0
    }
}

# ── Target Engine v7 — FINAL FROZEN ──────────────────────────────────────────

# For Enum typing, though Enum is defined in sl_target_helper.
# We will use string representations here to avoid circular imports, 
# or just redefine them if we need them, but it's better to keep strings in config 
# and map them to enums in the helper.
# Actually, the spec says "TARGET_SOURCE_WEIGHTS = { TargetSource.EQUAL_HIGH: 10 ... }"
# To do this cleanly without circular import, we can define the enum here or in a separate file.
# The spec puts the Enum in sl_target_helper.py. So we'll use strings in config and the engine will map/handle.
# Let's use the string names matching the enum keys.

TARGET_SOURCE_WEIGHTS = {
    "EQUAL_HIGH":     10,
    "RESISTANCE":     10,
    "HIGH_20D":        9,
    "PREV_DAY_HIGH":   9,
    "HIGH_52W":        8,
    "ABCD":            9,
    "RETRACE_50":      8,
    "RETRACE_618":     7,
    "RETRACE_382":     6,
    "FIB_127":         7,
    "FIB_162":         6,
    "SMA200":          8,
    "BB_MID":          7,
    "SMA50":           6,
    "FIB_200":         5,
    "ATR_PROJ":        4,
    "R1":              5,
    "R2":              4,
    "ROUND_NUM":       0,
}

FIB_200_WEIGHTS = {"BULL": 7, "TRENDING": 7, "NEUTRAL": 5, "BEAR": 2}

SOURCE_PRIORITY = {
    "EQUAL_HIGH":     1,
    "RESISTANCE":     2,
    "HIGH_20D":       3,
    "PREV_DAY_HIGH":  4,
    "HIGH_52W":       5,
    "ABCD":           6,
    "RETRACE_618":    7,
    "RETRACE_50":     8,
    "RETRACE_382":    9,
    "FIB_127":        10,
    "FIB_162":        11,
    "SMA200":         12,
    "SMA50":          13,
    "BB_MID":         14,
    "FIB_200":        15,
    "ATR_PROJ":       16,
    "R1":             17,
    "R2":             18,
    "ROUND_NUM":      99,
}

TARGET_CONFLICT_POLICY = {
    "EOD":      "REGIME",
    "MULTI_TF": "CONFIDENCE",
    "REVERSAL": "NEAREST",
}

EXIT_PROFILES = {
    "CONSERVATIVE": {"t1": 25, "t2": 50, "t3": 25},
    "BALANCED":     {"t1": 30, "t2": 40, "t3": 30},
    "AGGRESSIVE":   {"t1": 20, "t2": 30, "t3": 50},
}

SCANNER_EXIT_PROFILE = {
    "EOD":      "BALANCED",
    "MULTI_TF": "AGGRESSIVE",
    "REVERSAL": "CONSERVATIVE",
    "PULLBACK": "BALANCED",
}

FIB_EXTENSIONS   = [1.272, 1.618, 2.0]
FIB_RETRACEMENTS = [0.382, 0.500, 0.618]
ABCD_BC_RETRACE_MIN = 0.382
ABCD_BC_RETRACE_MAX = 0.786
FIB_200_GATE     = {"min_adx": 30, "min_vol_ratio": 2.0, "require_above_vwap": True}

ROUND_NUMBER_BOOST      = 8
ROUND_NUMBER_PCT        = 0.005
TARGET_CLUSTER_WINDOW_ATR_FRAC = 0.5
TARGET_CLUSTER_WINDOW_PCT      = 0.0075

#           atr_base  sl_atr_buf  sl_pct_buf  max_sl_atr
_MODE_CONFIG = {
    "EOD":      (2.00,    0.80,       0.0075,     3.0),
    "MULTI_TF": (1.50,    0.50,       0.0050,     3.0),
    "REVERSAL": (2.00,    1.00,       0.0100,     3.5),
}


SCANNER_MULTI_TF = "MULTI_TF"
