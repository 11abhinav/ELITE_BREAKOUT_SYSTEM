"""
app/short_covering_config.py

Configuration constants for Short-Covering Scanner.
Default Architecture: C5_INTRADAY_ONLY (Certified Production Champion)
Rollback Architecture: V1 (Legacy 2-Layer EOD Filtered Control)
"""

import os
from typing import Dict, Any

SHORT_COVERING_SCANNER_NAME = "SHORT_COVERING"
SHORT_COVERING_VERSION = "v3.0_C5_INTRADAY_ONLY"

# Engine selection switch with backward rollback capability
SHORT_COVERING_ENGINE = os.getenv("SHORT_COVERING_ENGINE", "C5_INTRADAY_ONLY").upper()

# ── DAILY ACTIVE F&O UNIVERSE BUILDER SETTINGS (Pre-09:05 IST) ───────────────
DAILY_UNIVERSE_CONFIG: Dict[str, Any] = {
    "MIN_DAILY_TURNOVER_CR": 5.0,        # Minimum daily turnover (₹5 Cr) for liquid F&O
    "MAX_BID_ASK_SPREAD_PCT": 0.15,      # Spread threshold
    "EXCLUDE_BANNED": True,              # Exclude securities in F&O ban period
    "FREEZE_BEFORE_TIME": "09:05",       # Universe frozen before 09:05 IST
}

# ── INTRADAY 5M IGNITION SCANNER SETTINGS (09:20 - 15:25 IST) ────────────────
INTRADAY_IGNITION_CONFIG: Dict[str, Any] = {
    "ENGINE": SHORT_COVERING_ENGINE,     # Default: C5_INTRADAY_ONLY
    "SCAN_INTERVAL_MINUTES": 5,          # Run every 5 minutes during 09:20 - 15:25 IST
    "SIGNAL_WINDOW_START": "09:20",      # Certified signal start window
    "SIGNAL_WINDOW_END": "15:25",        # Certified signal end window
    "MIN_5M_OI_CONTRACTION_PCT": -0.50,  # Certified 5m OI contraction (<= -0.50%)
    "MIN_5M_VOLUME_SURGE_RATIO": 2.00,   # Certified 5m RVOL surge (>= 2.0x 10-bar rolling mean)
    "MIN_CLV": 0.80,                     # Certified Close Location Value (>= 0.80)
    "MIN_IGNITION_SCORE": 65.0,          # Certified minimum ignition score (>= 65.0)
    "SYMBOL_COOLDOWN_MINUTES": 30,       # Deduplication guard: 30m symbol cooldown
    "MAX_PORTFOLIO_SLOTS": 10,           # Max concurrent portfolio positions (10 slots)
    "ROLLOVER_EXCLUSION_RATIO": 0.70,    # If next month OI absorbs >= 70% of drop in expiry week
}

# ── LEGACY V1 ROLLBACK CONFIG (Inactive under C5) ─────────────────────────────
LEGACY_V1_CONFIG: Dict[str, Any] = {
    "MIN_EOD_QUALITY_SCORE": 50.0,       # Legacy V1 EOD score gate
    "MAX_WATCHLIST_SIZE": 35,            # Legacy V1 35-symbol cap
}

# ── MASTER AGGREGATED CONFIG ────────────────────────────────────────────────
SHORT_COVERING_CONFIG: Dict[str, Any] = {
    "NAME": SHORT_COVERING_SCANNER_NAME,
    "VERSION": SHORT_COVERING_VERSION,
    "ENGINE": SHORT_COVERING_ENGINE,
    "UNIVERSE": DAILY_UNIVERSE_CONFIG,
    "INTRADAY": INTRADAY_IGNITION_CONFIG,
    "LEGACY_V1": LEGACY_V1_CONFIG,
}

