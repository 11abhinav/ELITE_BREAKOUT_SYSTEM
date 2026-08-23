"""
app/accumulation/config.py — Configuration parameters & thresholds for ACCUMULATION_SCANNER_V1.
Contains explicit constants, weights, piecewise score bounds, and account risk defaults.
"""

from typing import Dict, Any

# ── Version Tags ─────────────────────────────────────────────────────────────
STRATEGY_VERSION = "ACCUMULATION_V1.0"
SL_TARGET_VERSION = "ACCUM_SL_V1"
CONFIG_VERSION = "ACCUM_CFG_V1"
SCORE_NORMALIZATION_VERSION = "ACCUM_SCORE_NORM_V1"

# ── Isolation & Hard Floor Rules ──────────────────────────────────────────────
MIN_DAILY_BARS = 252
SECTOR_EXCEPTION_ENABLED = False  # Hard floor exemption explicitly disabled

# Fundamental Floor Hard Thresholds
FUNDAMENTAL_FLOOR = {
    "min_roe": 12.0,      # ROE >= 12.0%
    "min_roce": 15.0,     # ROCE >= 15.0%
    "max_de": 1.0,        # D/E <= 1.0
}

# Hard Component Score Gates (0–100 Scale)
HARD_COMPONENT_GATES = {
    "min_accumulation_score": 60.0,
    "min_compression_score": 50.0,
    "min_rs_score": 50.0,
    "max_resistance_dist_pct": 10.0,
    "min_resistance_score": 50.0,
    "min_delivery_score": 50.0,  # Delivery sub-score gate for BREAKOUT_READY
}

# Signal State Score Thresholds
SIGNAL_THRESHOLDS = {
    "ACCUMULATION_WATCH": 70.0,  # 70–77
    "PRE_BREAKOUT": 78.0,        # 78–84 (>= 2 contiguous days)
    "BREAKOUT_READY": 85.0,      # 85+ (>= 2 contiguous days + Delivery VALID + Distance Improving >= 0.25%)
}

# ── Composite Score Weights ──────────────────────────────────────────────────
COMPOSITE_WEIGHTS = {
    "accumulation": 0.25,
    "compression": 0.20,
    "relative_strength": 0.20,
    "resistance_structure": 0.15,
    "volume_delivery": 0.10,
    "fundamental": 0.10,
}

# ── Entry Activation & Gap Limits ─────────────────────────────────────────────
MAX_ENTRY_GAP_PCT = 0.02  # 2.0% maximum entry displacement gap
BREAKOUT_CONFIRMATION_BUFFER_PCT = 0.002  # 0.2% buffer above breakout level

# ── Initial Tradability Check Invariants ─────────────────────────────────────
INITIAL_TRADABILITY = {
    "min_rr_1": 2.0,       # natural_rr_1 >= 2.0
    "max_risk_pct": 8.0,   # risk_pct <= 8.0%
}

# ── Structural Stop Loss Engine Settings ──────────────────────────────────────
SL_CONFIG = {
    "min_stop_distance_atr": 0.80,  # 0.80x ATR safety floor
    "base_atr_buf": 0.50,           # 0.50x ATR buffer
    "max_sl_atr": 3.0,              # Max 3.0x ATR from entry
}

# ── Target Engine Settings ────────────────────────────────────────────────────
TARGET_CONFIG = {
    "min_natural_rr": 2.0,
    "measured_move_mult": 1.272,    # Target 3 minimum extension
}

# ── Capital & Position Sizing Defaults ────────────────────────────────────────
POSITION_SIZING_DEFAULTS = {
    "default_account_capital": 1000000.0,  # ₹10,00,000 reference account
    "account_risk_pct": 1.0,               # 1.0% account risk per setup
    "position_sizing_basis": "ACCOUNT_RISK_1PCT",
}

# ── Deduplication & Cooldown Settings ─────────────────────────────────────────
COOLDOWN_AFTER_TERMINAL_DAYS = 10
INVALIDATION_CONFIRMATION_DAYS = 2
RESISTANCE_IMPROVEMENT_MIN_PTS = 0.25  # Explicit 0.25-point requirement
