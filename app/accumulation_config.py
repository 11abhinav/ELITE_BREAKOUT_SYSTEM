"""
app/accumulation_config.py

Configuration constants for ACCUMULATION_SCANNER_V1.
Additive, isolated configuration for early accumulation and compression scanner.
"""

from typing import Dict, Any

ACCUMULATION_SCANNER_NAME = "ACCUMULATION"
ACCUMULATION_VERSION = "v1.0"
ACCUMULATION_DEFAULT_BATCH_SIZE = 50

# ── SCORING WEIGHTS (Total = 100) ──────────────────────────────────────────
ACCUMULATION_WEIGHTS: Dict[str, float] = {
    "INSTITUTIONAL_ACCUMULATION": 30.0,
    "VOLATILITY_COMPRESSION": 20.0,
    "RELATIVE_STRENGTH": 15.0,
    "RESISTANCE_PROXIMITY": 15.0,
    "VOLUME_DELIVERY_STRUCTURE": 10.0,
    "FUNDAMENTAL_QUALITY_FLOOR": 10.0,
}

# ── STATE CLASSIFICATION THRESHOLDS ───────────────────────────────────────
STATE_THRESHOLDS: Dict[str, float] = {
    "ACCUMULATION_WATCH": 70.0,
    "PRE_BREAKOUT": 78.0,
    "BREAKOUT_READY": 85.0,
}

# ── FUNDAMENTAL QUALITY FLOOR DEFAULT PARAMETERS ──────────────────────────
FUNDAMENTAL_FLOOR_CONFIG: Dict[str, float] = {
    "MIN_ROE": 12.0,            # 12%
    "MIN_ROCE": 15.0,           # 15%
    "MAX_DEBT_EQUITY": 1.0,     # D/E < 1.0
    "MIN_SALES_GROWTH": 8.0,    # 8%
    "MIN_PAT_GROWTH": 8.0,      # 8%
}

# ── STRUCTURAL SL & TARGET PARAMETERS ─────────────────────────────────────
SL_TARGET_CONFIG: Dict[str, Any] = {
    "ATR_SAFETY_BUFFER": 0.50,            # 0.50 * ATR safety buffer
    "MIN_INITIAL_RR": 2.0,                # Minimum initial R:R required for tradable setup
    "MAX_ACCUMULATION_HOLD_DAYS": 40,     # Maximum hold days before Time Stop
    "CONFIRMATION_BUFFER_PCT": 0.005,     # 0.5% breakout confirmation buffer
    "EARLY_ENTRY_ZONE_RANGE_PCT": 0.015,  # 1.5% entry zone width below breakout
}

# ── TECHNICAL INDICATOR LOOKBACKS ──────────────────────────────────────────
TECHNICAL_LOOKBACKS: Dict[str, int] = {
    "OBV_SLOPE_BARS": 20,
    "AD_TREND_BARS": 20,
    "ATR_LOOKBACK": 14,
    "RSI_LOOKBACK": 14,
    "BB_WIDTH_LOOKBACK": 20,
    "RANGE_COMPRESSION_BARS": 20,
    "RS_SHORT_BARS": 20,                  # 20D RS
    "RS_LONG_BARS": 60,                   # 60D RS
}
