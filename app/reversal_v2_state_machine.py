# =============================================================================
# app/reversal_v2_state_machine.py
# REVERSAL V2: 5-STAGE STRUCTURAL SELECTION FUNNEL (Challenger Engine)
# =============================================================================
#
# DOCTRINE
# --------
# The Reversal Engine goal is NOT a high win-rate screener.
# Its purpose is to identify RARE structural selling-exhaustion events that
# carry asymmetric payoff potential (+3R to +10R).
#
# Stage 1 = candidate discovery only. Stages 2-5 = progressive proof that the
# selling regime has structurally failed and buyers are taking control.
#
# CHAMPION vs CHALLENGER MAPPING
# --------------------------------
#   V1 Champion (REVERSAL_CHAMPION_V1): Raw RSI oversold (E[R]=+0.03R, PF=1.06)
#   V2-A Challenger (REVERSAL_V2A_SWEEP_RECLAIM):  Stages 1-2 — Sweep + Reclaim
#   V2-B Challenger (REVERSAL_V2B_DISPLACEMENT):   Stages 1-3 — + Bullish Displacement
#   V2-C Challenger (REVERSAL_V2C_HIGHER_LOW):     Stages 1-4 — + Higher Low structure
#   V2-D Challenger (REVERSAL_V2D_FULL_FUNNEL):    All 5 stages + regime + R:R gate
#
# 5-STAGE STATE MACHINE
# ----------------------
#   Stage 1: EXHAUSTION_CANDIDATE  — RSI oversold + min drop. NO TRADE.
#   Stage 2: SWEEP_SETUP           — Climax volume surge on new low. NO TRADE.
#   Stage 3: RECLAIM_CONFIRMED     — Price closes back above swept level (Spring).
#   Stage 4: DISPLACEMENT_BURST    — Large bullish candle (body >50%, close top 25%).
#   Stage 5: HIGH_CONFIDENCE_ALERT — HL structure + regime allowed + R:R >= threshold.
#
# LOOKAHEAD CONTRACT
# ------------------
# All lookback calculations use df.iloc[:-1] (prior CLOSED bars only).
# The current bar (df.iloc[-1]) is the ACTION bar.
# No feature may reference the current bar's price in prior-bar calculations.
# =============================================================================

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("ReversalV2StateMachine")


# ---------------------------------------------------------------------------
# Stage enum
# ---------------------------------------------------------------------------

class ReversalStage(str, Enum):
    NO_SETUP              = "NO_SETUP"
    EXHAUSTION_CANDIDATE  = "EXHAUSTION_CANDIDATE"   # Stage 1 — discovery only
    SWEEP_SETUP           = "SWEEP_SETUP"             # Stage 2 — climax volume sweep
    RECLAIM_CONFIRMED     = "RECLAIM_CONFIRMED"       # Stage 3 — failed breakdown / spring
    DISPLACEMENT_BURST    = "DISPLACEMENT_BURST"      # Stage 4 — large bullish candle
    HIGH_CONFIDENCE_ALERT = "HIGH_CONFIDENCE_ALERT"   # Stage 5 — full funnel cleared


# ---------------------------------------------------------------------------
# Configuration (all thresholds are candidate parameters)
# ---------------------------------------------------------------------------

@dataclass
class ReversalV2Config:
    """
    All thresholds are CANDIDATE parameters under champion/challenger testing.
    Do NOT assume any value is optimal until walk-forward validated.
    """
    # Stage 1: Exhaustion detection
    min_drop_from_52w_high_pct: float = 12.0   # Min drawdown from 52-week high
    max_drop_from_52w_high_pct: float = 60.0   # Cap — beyond 60% signals structural impairment
    rsi_oversold_threshold:     float = 35.0   # RSI floor for oversold candidacy
    min_price_floor:            float = 100.0  # Price floor in Rs

    # Stage 2: Liquidity sweep (climax volume)
    sweep_volume_ratio:         float = 2.0    # Current bar volume vs 20D avg (prior bars only)
    min_sweep_candle_size_atr:  float = 1.5    # Sweep candle must be >= 1.5x ATR to be a climax bar

    # Stage 3: Reclaim (Spring / Failed Breakdown)
    reclaim_bars_window:        int   = 5      # Look for reclaim within N prior bars

    # Stage 4: Displacement burst
    min_displacement_body_ratio: float = 0.50  # Body must be > 50% of bar range
    min_displacement_close_pos:  float = 0.70  # Close must be in top 30% of bar range

    # Stage 5: High-confidence alert
    require_higher_low:          bool  = True  # Price must form HL vs sweep low
    min_rr_ratio:                float = 2.5   # Minimum Risk:Reward to first major resistance
    allowed_regimes:             List[str] = field(default_factory=lambda: ["BULL", "NEUTRAL"])
    nq_universe_blocks_stage5:   bool  = True  # NQ stocks capped at Stage 3 (observation only)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ReversalV2Result:
    """Complete output from the 5-stage funnel for one symbol on one day."""
    symbol:             str
    stage:              ReversalStage
    variant_id:         str           = "REVERSAL_V2D_FULL_FUNNEL"
    live_trade_allowed: bool          = False

    engine_path:        str           = ""
    reasons:            List[str]     = field(default_factory=list)
    score:              float         = 0.0
    quality_grade:      str           = "C"

    # Trade structure (populated from Stage 3 onward)
    entry_price:        Optional[float] = None
    stop_loss:          Optional[float] = None
    target_1:           Optional[float] = None
    rr_ratio:           Optional[float] = None
    breakout_level:     Optional[float] = None

    # Per-stage diagnostic markers
    stage1_rsi:                Optional[float] = None
    stage1_drop_pct:            Optional[float] = None
    stage2_sweep_vol_ratio:     Optional[float] = None
    stage2_sweep_low:           Optional[float] = None
    stage3_reclaim_close:       Optional[float] = None
    stage4_body_ratio:          Optional[float] = None
    stage4_close_pos:           Optional[float] = None
    stage5_higher_low:          Optional[bool]  = None

    is_nq_universe:     bool   = False
    setup_id:           str    = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol":                   self.symbol,
            "stage":                    self.stage.value,
            "variant_id":               self.variant_id,
            "live_trade_allowed":       self.live_trade_allowed,
            "engine_path":              self.engine_path,
            "reason":                   "; ".join(self.reasons),
            "score":                    self.score,
            "quality_grade":            self.quality_grade,
            "entry_price":              self.entry_price,
            "stop_loss":                self.stop_loss,
            "target_1":                 self.target_1,
            "rr_ratio":                 self.rr_ratio,
            "breakout_level":           self.breakout_level,
            "stage1_rsi":               self.stage1_rsi,
            "stage1_drop_pct":          self.stage1_drop_pct,
            "stage2_sweep_vol_ratio":   self.stage2_sweep_vol_ratio,
            "stage2_sweep_low":         self.stage2_sweep_low,
            "stage3_reclaim_close":     self.stage3_reclaim_close,
            "stage4_body_ratio":        self.stage4_body_ratio,
            "stage4_close_pos":         self.stage4_close_pos,
            "stage5_higher_low":        self.stage5_higher_low,
            "is_nq_universe":           self.is_nq_universe,
            "setup_id":                 self.setup_id,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_rsi(close_series: pd.Series, period: int = 14) -> float:
    """
    Zero-lookahead RSI. Series must already be sliced to prior closed bars only
    (i.e., df.iloc[:-1]["Close"]) before being passed here.
    """
    delta    = close_series.diff()
    avg_gain = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    avg_loss = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, 1e-9)
    rsi      = 100.0 - (100.0 / (1.0 + rs))
    val      = float(rsi.iloc[-1]) if len(rsi) > 0 else 50.0
    return round(max(0.0, min(100.0, val)), 1)


# ---------------------------------------------------------------------------
# Main evaluation function
# ---------------------------------------------------------------------------

def evaluate_reversal_v2(
    symbol: str,
    df: pd.DataFrame,
    config: Optional[ReversalV2Config] = None,
    fund_data: Optional[Dict[str, Any]] = None,
    is_nq_universe: bool = False,
    macro_regime: str = "NEUTRAL",
    target_resistance: Optional[float] = None,
) -> ReversalV2Result:
    """
    Full 5-stage Reversal V2 evaluation for one symbol on one evaluation day.

    Called daily on each candidate. Returns the CURRENT highest stage
    the symbol has progressed to. Higher stages = more complete proof
    of selling exhaustion and structural reversal.

    Parameters
    ----------
    symbol            : Ticker symbol
    df                : OHLCV DataFrame (DatetimeIndex). Needs >= 50 closed bars.
    config            : ReversalV2Config instance (uses defaults if None).
    fund_data         : Optional fundamentals dict (roe, Category, etc.)
    is_nq_universe    : True if stock is in Near-Qualified universe
    macro_regime      : Current macro regime string (BULL / NEUTRAL / BEAR)
    target_resistance : Next major resistance for R:R calc.
                        If None, uses prior 60-bar high as proxy (zero-lookahead).
    """
    cfg = config or ReversalV2Config()

    def _no_setup(reason: str) -> ReversalV2Result:
        return ReversalV2Result(
            symbol=symbol, stage=ReversalStage.NO_SETUP,
            reasons=[reason], is_nq_universe=is_nq_universe
        )

    # ── Data sufficiency ─────────────────────────────────────────────────────
    if df is None or len(df) < 50:
        return _no_setup("INSUFFICIENT_DATA: Need >= 50 closed bars.")

    # Action bar (today)
    latest = df.iloc[-1]
    close  = float(latest["Close"])
    open_p = float(latest["Open"])
    high_p = float(latest["High"])
    low_p  = float(latest["Low"])
    volume = float(latest["Volume"]) if "Volume" in df.columns else 0.0

    # All lookback references use prior closed bars only (zero-lookahead)
    prior = df.iloc[:-1]

    # ── Price floor ───────────────────────────────────────────────────────────
    if close < cfg.min_price_floor:
        return _no_setup(
            f"PRICE_FLOOR: Close Rs{close:.2f} < minimum Rs{cfg.min_price_floor:.2f}."
        )

    # =========================================================================
    # STAGE 1: EXHAUSTION CANDIDATE
    # Criteria: sufficient drawdown from 52-week high + RSI oversold.
    # Action  : Candidate discovery ONLY. NO trade generated.
    # =========================================================================
    high_52w = (
        float(prior["High"].iloc[-249:].max()) if len(prior) >= 249
        else float(prior["High"].max())
    )
    drop_pct = (high_52w - close) / high_52w * 100.0 if high_52w > 0 else 0.0

    if not (cfg.min_drop_from_52w_high_pct <= drop_pct <= cfg.max_drop_from_52w_high_pct):
        return _no_setup(
            f"STAGE1_FAIL: Drop {drop_pct:.1f}% outside "
            f"[{cfg.min_drop_from_52w_high_pct:.0f}%, {cfg.max_drop_from_52w_high_pct:.0f}%] band."
        )

    current_rsi = _compute_rsi(prior["Close"])
    if current_rsi > cfg.rsi_oversold_threshold:
        return _no_setup(
            f"STAGE1_FAIL: RSI {current_rsi:.1f} > {cfg.rsi_oversold_threshold:.0f} threshold."
        )

    stage1_result = ReversalV2Result(
        symbol=symbol,
        stage=ReversalStage.EXHAUSTION_CANDIDATE,
        live_trade_allowed=False,
        engine_path="STAGE1_EXHAUSTION",
        reasons=[f"Stage1 Pass: Drop {drop_pct:.1f}% from 52W high, RSI {current_rsi:.1f}"],
        score=40.0, quality_grade="C",
        stage1_rsi=current_rsi,
        stage1_drop_pct=round(drop_pct, 1),
        is_nq_universe=is_nq_universe,
        setup_id=f"REV2_{symbol}_{date.today()}",
    )

    # =========================================================================
    # STAGE 2: LIQUIDITY SWEEP (Climax Volume)
    # Criteria: abnormal selling volume on a new multi-week low (climax bar).
    # Action  : Still NO trade. Selling may not be exhausted yet.
    # =========================================================================
    avg_vol_20d = (
        float(prior["Volume"].iloc[-20:].mean())
        if ("Volume" in prior.columns and len(prior) >= 20) else 1.0
    )
    vol_ratio = (volume / avg_vol_20d) if avg_vol_20d > 0 else 1.0

    # ATR from prior bars only
    true_ranges = (prior["High"] - prior["Low"]).abs()
    atr_14 = (
        float(true_ranges.iloc[-14:].mean()) if len(true_ranges) >= 14
        else float(true_ranges.mean())
    )

    candle_range          = max(0.001, high_p - low_p)
    sweep_candle_size_atr = candle_range / atr_14 if atr_14 > 0 else 0.0

    prior_25_low = (
        float(prior["Low"].iloc[-25:].min()) if len(prior) >= 25
        else float(prior["Low"].min())
    )
    # Current bar undercuts the prior 25-bar range (climax sweep)
    is_new_low = low_p <= prior_25_low * 1.001

    stage2_passed = (
        vol_ratio >= cfg.sweep_volume_ratio
        and sweep_candle_size_atr >= cfg.min_sweep_candle_size_atr
        and is_new_low
    )

    if not stage2_passed:
        stage1_result.reasons.append(
            f"Stage2 pending: vol_ratio={vol_ratio:.2f} "
            f"(need >={cfg.sweep_volume_ratio:.1f}), "
            f"sweep_size={sweep_candle_size_atr:.2f}x ATR "
            f"(need >={cfg.min_sweep_candle_size_atr:.1f}), "
            f"new_low={is_new_low}"
        )
        return stage1_result

    sweep_low = low_p  # Current bar's low anchors the sweep level

    stage2_result = ReversalV2Result(
        symbol=symbol,
        stage=ReversalStage.SWEEP_SETUP,
        live_trade_allowed=False,
        engine_path="STAGE2_SWEEP",
        reasons=[
            f"Stage1 Pass: Drop {drop_pct:.1f}%, RSI {current_rsi:.1f}",
            f"Stage2 Pass: Climax vol {vol_ratio:.2f}x 20D avg, sweep low Rs{sweep_low:.2f}",
        ],
        score=55.0, quality_grade="C",
        stage1_rsi=current_rsi,
        stage1_drop_pct=round(drop_pct, 1),
        stage2_sweep_vol_ratio=round(vol_ratio, 2),
        stage2_sweep_low=round(sweep_low, 2),
        is_nq_universe=is_nq_universe,
        setup_id=f"REV2_{symbol}_{date.today()}",
    )

    # =========================================================================
    # STAGE 3: RECLAIM CONFIRMED (Spring / Failed Breakdown)
    # Criteria: price CLOSES ABOVE the swept support level.
    #           Sellers absorbed; buyers reclaim the level — classic spring.
    # Action  : Trade structure defined. entry ~ close, SL ~ below sweep low.
    # =========================================================================
    prior_sweep_low = (
        float(prior["Low"].iloc[-cfg.reclaim_bars_window:].min())
        if len(prior) >= cfg.reclaim_bars_window
        else float(prior["Low"].min())
    )
    # Close must be materially above the prior sweep low
    is_reclaim = close > prior_sweep_low * 1.001

    if not is_reclaim:
        stage2_result.reasons.append(
            f"Stage3 pending: Close Rs{close:.2f} not yet above "
            f"prior sweep low Rs{prior_sweep_low:.2f}"
        )
        return stage2_result

    # Define trade structure from the reclaim bar
    stop_loss   = round(prior_sweep_low * 0.995, 2)   # SL just below sweep low
    entry_price = round(close, 2)
    risk_dist   = max(0.01, entry_price - stop_loss)

    # Target 1: prior 10-bar swing high (zero-lookahead)
    prior_10d_high = (
        float(prior["High"].iloc[-10:].max()) if len(prior) >= 10
        else float(prior["High"].max())
    )
    target_1 = round(prior_10d_high, 2)
    rr_ratio  = round((target_1 - entry_price) / risk_dist, 2) if risk_dist > 0 else 0.0

    stage3_result = ReversalV2Result(
        symbol=symbol,
        stage=ReversalStage.RECLAIM_CONFIRMED,
        live_trade_allowed=(not is_nq_universe) and rr_ratio >= 1.5,
        engine_path="STAGE3_RECLAIM",
        reasons=[
            f"Stage1 Pass: Drop {drop_pct:.1f}%, RSI {current_rsi:.1f}",
            f"Stage2 Pass: Climax vol {vol_ratio:.2f}x, sweep low Rs{prior_sweep_low:.2f}",
            f"Stage3 Pass: Reclaim Rs{close:.2f} > sweep low. "
            f"SL=Rs{stop_loss:.2f}, T1=Rs{target_1:.2f}, R:R={rr_ratio:.1f}",
        ],
        score=65.0, quality_grade="B",
        stage1_rsi=current_rsi,
        stage1_drop_pct=round(drop_pct, 1),
        stage2_sweep_vol_ratio=round(vol_ratio, 2),
        stage2_sweep_low=round(prior_sweep_low, 2),
        stage3_reclaim_close=round(close, 2),
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_1=target_1,
        rr_ratio=rr_ratio,
        is_nq_universe=is_nq_universe,
        setup_id=f"REV2_{symbol}_{date.today()}",
    )

    # NQ universe capped at Stage 3 (observation only — not a live trade)
    if is_nq_universe and cfg.nq_universe_blocks_stage5:
        stage3_result.live_trade_allowed = False
        stage3_result.reasons.append(
            "NQ_UNIVERSE: Capped at Stage 3 (observation only, no live trade)."
        )
        return stage3_result

    # =========================================================================
    # STAGE 4: DISPLACEMENT BURST
    # Criteria: large bullish candle (body > min_body_ratio of range, close in
    #           top portion). Confirms buyers are aggressively participating,
    #           not merely short-covering.
    # =========================================================================
    body_ratio = abs(close - open_p) / candle_range if candle_range > 0 else 0.0
    close_pos  = (close - low_p)   / candle_range if candle_range > 0 else 0.5
    is_bullish = close > open_p

    stage4_passed = (
        is_bullish
        and body_ratio >= cfg.min_displacement_body_ratio
        and close_pos  >= cfg.min_displacement_close_pos
    )

    if not stage4_passed:
        stage3_result.reasons.append(
            f"Stage4 pending: body_ratio={body_ratio:.2f} "
            f"(need >={cfg.min_displacement_body_ratio:.2f}), "
            f"close_pos={close_pos:.2f} "
            f"(need >={cfg.min_displacement_close_pos:.2f}), "
            f"bullish={is_bullish}"
        )
        return stage3_result

    stage4_result = ReversalV2Result(
        symbol=symbol,
        stage=ReversalStage.DISPLACEMENT_BURST,
        live_trade_allowed=rr_ratio >= 1.5,
        engine_path="STAGE4_DISPLACEMENT",
        reasons=[
            f"Stage1 Pass: Drop {drop_pct:.1f}%, RSI {current_rsi:.1f}",
            f"Stage2 Pass: Climax vol {vol_ratio:.2f}x, sweep low Rs{prior_sweep_low:.2f}",
            f"Stage3 Pass: Reclaim Rs{close:.2f}. "
            f"SL=Rs{stop_loss:.2f}, T1=Rs{target_1:.2f}, R:R={rr_ratio:.1f}",
            f"Stage4 Pass: Displacement — body={body_ratio:.2f}, close_pos={close_pos:.2f}",
        ],
        score=78.0, quality_grade="B",
        stage1_rsi=current_rsi,
        stage1_drop_pct=round(drop_pct, 1),
        stage2_sweep_vol_ratio=round(vol_ratio, 2),
        stage2_sweep_low=round(prior_sweep_low, 2),
        stage3_reclaim_close=round(close, 2),
        stage4_body_ratio=round(body_ratio, 2),
        stage4_close_pos=round(close_pos, 2),
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_1=target_1,
        rr_ratio=rr_ratio,
        is_nq_universe=is_nq_universe,
        setup_id=f"REV2_{symbol}_{date.today()}",
    )

    # =========================================================================
    # STAGE 5: HIGH-CONFIDENCE ALERT
    # Criteria:
    #   (a) Higher Low formed — prior bar low > sweep low (structural HL)
    #   (b) Macro regime in allowed list (BULL / NEUTRAL by default)
    #   (c) R:R to first major resistance >= min_rr_ratio
    # Action : FULL HIGH_CONFIDENCE_ALERT — live_trade_allowed = True
    # =========================================================================
    stage5_fails = []

    # (a) Higher Low: prior bar's low must be above the sweep low
    higher_low_confirmed = True
    if cfg.require_higher_low:
        prev_bar_low      = float(prior["Low"].iloc[-1]) if len(prior) >= 1 else low_p
        higher_low_confirmed = prev_bar_low > prior_sweep_low * 1.001
        if not higher_low_confirmed:
            stage5_fails.append(
                f"Stage5 HL Fail: Prior bar low Rs{prev_bar_low:.2f} "
                f"not above sweep low Rs{prior_sweep_low:.2f}"
            )

    # (b) Macro regime check
    regime_ok = macro_regime.upper() in [r.upper() for r in cfg.allowed_regimes]
    if not regime_ok:
        stage5_fails.append(
            f"Stage5 Regime Fail: {macro_regime} not in allowed {cfg.allowed_regimes}"
        )

    # (c) R:R validation against first major resistance (zero-lookahead fallback)
    resistance = target_resistance
    if resistance is None:
        resistance = (
            float(prior["High"].iloc[-60:].max()) if len(prior) >= 60
            else float(prior["High"].max())
        )

    final_rr = round((resistance - entry_price) / risk_dist, 2) if risk_dist > 0 else 0.0
    rr_ok    = final_rr >= cfg.min_rr_ratio
    if not rr_ok:
        stage5_fails.append(
            f"Stage5 RR Fail: R:R={final_rr:.1f} < minimum {cfg.min_rr_ratio:.1f} "
            f"to resistance Rs{resistance:.2f}"
        )

    if stage5_fails:
        # Failed one or more Stage 5 gates — return Stage 4 with pending reasons
        stage4_result.reasons.extend(stage5_fails)
        return stage4_result

    # All 5 stages cleared
    score = 92.0 if final_rr >= 3.5 else 85.0
    grade = "A+" if score >= 90.0 else "A"

    stage5_reasons = list(stage4_result.reasons)
    stage5_reasons.append(
        f"Stage5 Pass: HL confirmed, regime={macro_regime}, "
        f"R:R={final_rr:.1f}x to resistance Rs{resistance:.2f}"
    )

    return ReversalV2Result(
        symbol=symbol,
        stage=ReversalStage.HIGH_CONFIDENCE_ALERT,
        live_trade_allowed=True,
        engine_path="STAGE5_HIGH_CONFIDENCE",
        reasons=stage5_reasons,
        score=score,
        quality_grade=grade,
        stage1_rsi=current_rsi,
        stage1_drop_pct=round(drop_pct, 1),
        stage2_sweep_vol_ratio=round(vol_ratio, 2),
        stage2_sweep_low=round(prior_sweep_low, 2),
        stage3_reclaim_close=round(close, 2),
        stage4_body_ratio=round(body_ratio, 2),
        stage4_close_pos=round(close_pos, 2),
        stage5_higher_low=higher_low_confirmed,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_1=round(resistance, 2),
        rr_ratio=final_rr,
        breakout_level=round(prior_sweep_low, 2),
        is_nq_universe=is_nq_universe,
        setup_id=f"REV2_{symbol}_{date.today()}",
    )


# =============================================================================
# REVERSAL V2.1: MULTI-BAR STATE TRACKING (Challenger Engine)
# =============================================================================
#
# RULE 67 CHANGE-RATIONALE:
# In the Release 4 Reversal replay, the single-candle V2 funnel suffered severe
# attrition at the Stage-2 -> Stage-3 transition (116 sweeps -> 16 same-candle reclaims,
# an 86.2% drop). Furthermore, V2-A exhibited a 71.4% post-SL recovery rate, indicating
# that entries were directionally sound but stop timing was suffocated by requiring
# same-bar climax and reclaim.
#
# V2.1 RELAXATION CONTRACT:
# 1. Sweep / climax occurs on bar N (exhaustion drop 12-60%, RSI <= 35, 25-bar low
#    undercut with volume >= 2.0x 20D avg and candle size >= 1.5x ATR).
# 2. Reversal candidate state persists across bars N+1 ... N+5.
# 3. Bar M (where M in [N, N+5]) qualifies as Stage 3 RECLAIM if:
#    - Close_M > swept_support_level * 1.001
#    - Prior bars between sweep and M had NOT already reclaimed (first reclaim bar).
# 4. Stop loss anchors dynamically to the sequence low: min(Low[N : M+1]) * 0.995.
# 5. ZERO LOOKAHEAD: All evaluations use only df.iloc[:M+1] (past and current bar only).
# =============================================================================

def evaluate_reversal_v2_1(
    symbol: str,
    df: pd.DataFrame,
    config: Optional[ReversalV2Config] = None,
    fund_data: Optional[Dict[str, Any]] = None,
    is_nq_universe: bool = False,
    macro_regime: str = "NEUTRAL",
    target_resistance: Optional[float] = None,
) -> ReversalV2Result:
    """
    Evaluates symbol under Reversal V2.1 multi-bar state tracking.

    Permits a Stage 2 climax sweep on bar N to be reclaimed on bars N through N+5.
    Returns ReversalV2Result representing the current highest stage achieved on the
    action bar (df.iloc[-1]).
    """
    cfg = config or ReversalV2Config()

    def _no_setup(reason: str) -> ReversalV2Result:
        return ReversalV2Result(
            symbol=symbol, stage=ReversalStage.NO_SETUP,
            reasons=[reason], is_nq_universe=is_nq_universe,
            variant_id="REVERSAL_V21D_FULL_FUNNEL",
        )

    # ── Data sufficiency ─────────────────────────────────────────────────────
    if df is None or len(df) < 50:
        return _no_setup("INSUFFICIENT_DATA: Need >= 50 closed bars.")

    M = len(df) - 1
    action_bar = df.iloc[M]
    close_m  = float(action_bar["Close"])
    open_m   = float(action_bar["Open"])
    high_m   = float(action_bar["High"])
    low_m    = float(action_bar["Low"])
    volume_m = float(action_bar["Volume"]) if "Volume" in df.columns else 0.0

    if close_m < cfg.min_price_floor:
        return _no_setup(f"PRICE_FLOOR: Close Rs{close_m:.2f} < minimum Rs{cfg.min_price_floor:.2f}.")

    # Vectorized zero-lookahead feature series across df for candidate search
    close_s = df["Close"]
    high_s  = df["High"]
    low_s   = df["Low"]
    vol_s   = df["Volume"] if "Volume" in df.columns else pd.Series(0.0, index=df.index)

    # 1. Zero-lookahead 52-week high (prior to each bar)
    high_52w_s = high_s.shift(1).rolling(249, min_periods=10).max()
    drop_pct_s = (high_52w_s - close_s) / high_52w_s.replace(0, 1e-9) * 100.0

    # 2. Zero-lookahead RSI (computed once across full series)
    delta_s = close_s.diff()
    gain_s  = delta_s.clip(lower=0).ewm(span=14, adjust=False).mean()
    loss_s  = (-delta_s.clip(upper=0)).ewm(span=14, adjust=False).mean()
    rs_s    = gain_s / loss_s.replace(0, 1e-9)
    rsi_s   = (100.0 - (100.0 / (1.0 + rs_s))).round(1)

    # 3. Zero-lookahead 20-bar average volume
    vol_avg_20_s = vol_s.shift(1).rolling(20, min_periods=5).mean()
    vol_ratio_s  = vol_s / vol_avg_20_s.replace(0, 1e-9)

    # 4. Zero-lookahead 14-bar ATR and candle size
    tr_s         = (high_s - low_s).abs()
    atr_14_s     = tr_s.shift(1).rolling(14, min_periods=5).mean()
    sweep_size_s = (high_s - low_s) / atr_14_s.replace(0, 1e-9)

    # 5. Zero-lookahead 25-bar lowest low
    prior_25_low_s = low_s.shift(1).rolling(25, min_periods=10).min()

    # Precompute current bar Stage 1 baseline for diagnostic reporting
    drop_pct_m    = float(drop_pct_s.iloc[M]) if not pd.isna(drop_pct_s.iloc[M]) else 0.0
    current_rsi_m = float(rsi_s.iloc[M - 1])  if M >= 1 and not pd.isna(rsi_s.iloc[M - 1]) else 50.0

    # ── Search for candidate sweep in lookback window [M - reclaim_bars_window, M] ──
    window_bars = max(0, cfg.reclaim_bars_window)
    earliest_sweep_idx = max(25, M - window_bars)

    best_sweep_idx: Optional[int] = None
    best_swept_level: Optional[float] = None
    best_sweep_low: Optional[float] = None
    best_sweep_vol_ratio: Optional[float] = None

    # Search from most recent candidate bar backward to find the latest valid sweep
    for n in range(M, earliest_sweep_idx - 1, -1):
        if n < 25:
            continue

        drop_n = float(drop_pct_s.iloc[n])
        if not (cfg.min_drop_from_52w_high_pct <= drop_n <= cfg.max_drop_from_52w_high_pct):
            continue

        rsi_n = float(rsi_s.iloc[n - 1]) if n >= 1 else 50.0
        if rsi_n > cfg.rsi_oversold_threshold:
            continue

        vol_ratio_n = float(vol_ratio_s.iloc[n])
        if vol_ratio_n < cfg.sweep_volume_ratio:
            continue

        sweep_size_n = float(sweep_size_s.iloc[n])
        if sweep_size_n < cfg.min_sweep_candle_size_atr:
            continue

        prior_25_low_n = float(prior_25_low_s.iloc[n])
        l_n = float(low_s.iloc[n])
        if l_n > prior_25_low_n * 1.001:
            continue

        # Found a qualifying Stage 2 sweep at bar n
        best_sweep_idx       = n
        best_swept_level     = prior_25_low_n
        best_sweep_low       = l_n
        best_sweep_vol_ratio = vol_ratio_n
        break

    # If no sweep found in window, check if action bar M is at least Stage 1
    if best_sweep_idx is None:
        if (cfg.min_drop_from_52w_high_pct <= drop_pct_m <= cfg.max_drop_from_52w_high_pct
                and current_rsi_m <= cfg.rsi_oversold_threshold):
            return ReversalV2Result(
                symbol=symbol,
                stage=ReversalStage.EXHAUSTION_CANDIDATE,
                variant_id="REVERSAL_V21D_FULL_FUNNEL",
                live_trade_allowed=False,
                engine_path="V21_STAGE1_EXHAUSTION",
                reasons=[f"V2.1 Stage1: Drop {drop_pct_m:.1f}%, RSI {current_rsi_m:.1f}"],
                score=40.0, quality_grade="C",
                stage1_rsi=current_rsi_m,
                stage1_drop_pct=round(drop_pct_m, 1),
                is_nq_universe=is_nq_universe,
                setup_id=f"REV21_{symbol}_{date.today()}",
            )
        return _no_setup(f"STAGE1_OR_SWEEP_FAIL: No sweep in prior {cfg.reclaim_bars_window} bars, drop={drop_pct_m:.1f}%, rsi={current_rsi_m:.1f}")

    # A sweep was found at best_sweep_idx.
    # Check if action bar M confirms reclaim (Stage 3)
    swept_support = best_swept_level
    is_reclaim_on_m = close_m > swept_support * 1.001

    # In multi-bar state tracking: if sweep occurred on bar N < M,
    # did an intermediate bar already reclaim? If an earlier bar already reclaimed,
    # then bar M is late / post-reclaim.
    if best_sweep_idx < M:
        already_reclaimed = False
        for j in range(best_sweep_idx, M):
            if float(df["Close"].iloc[j]) > swept_support * 1.001:
                already_reclaimed = True
                break
        if already_reclaimed:
            # Reclaim occurred on an earlier bar; action bar M is not the initial reclaim entry
            return ReversalV2Result(
                symbol=symbol,
                stage=ReversalStage.SWEEP_SETUP,
                variant_id="REVERSAL_V21D_FULL_FUNNEL",
                live_trade_allowed=False,
                engine_path="V21_STAGE2_EXPIRED_RECLAIM",
                reasons=[f"V2.1 Stage2 Sweep at bar {best_sweep_idx} already reclaimed on prior bar."],
                score=50.0, quality_grade="C",
                stage1_rsi=current_rsi_m,
                stage1_drop_pct=round(drop_pct_m, 1),
                stage2_sweep_vol_ratio=round(best_sweep_vol_ratio or 1.0, 2),
                stage2_sweep_low=round(best_sweep_low or 0.0, 2),
                is_nq_universe=is_nq_universe,
                setup_id=f"REV21_{symbol}_{date.today()}",
            )

    if not is_reclaim_on_m:
        # Sweep active but action bar M has not reclaimed swept support
        bars_since_sweep = M - best_sweep_idx
        return ReversalV2Result(
            symbol=symbol,
            stage=ReversalStage.SWEEP_SETUP,
            variant_id="REVERSAL_V21D_FULL_FUNNEL",
            live_trade_allowed=False,
            engine_path="V21_STAGE2_PENDING_RECLAIM",
            reasons=[
                f"V2.1 Stage2 Sweep active ({bars_since_sweep} bars ago, sweep low Rs{best_sweep_low:.2f}). "
                f"Close Rs{close_m:.2f} <= swept support Rs{swept_support:.2f}."
            ],
            score=55.0, quality_grade="C",
            stage1_rsi=current_rsi_m,
            stage1_drop_pct=round(drop_pct_m, 1),
            stage2_sweep_vol_ratio=round(best_sweep_vol_ratio or 1.0, 2),
            stage2_sweep_low=round(best_sweep_low or 0.0, 2),
            is_nq_universe=is_nq_universe,
            setup_id=f"REV21_{symbol}_{date.today()}",
        )

    # ── STAGE 3: RECLAIM CONFIRMED ───────────────────────────────────────────
    # Sequence lowest point defines dynamic protective stop-loss
    sequence_low = float(df["Low"].iloc[best_sweep_idx : M + 1].min())
    stop_loss    = round(sequence_low * 0.995, 2)
    entry_price  = round(close_m, 2)
    risk_dist    = max(0.01, entry_price - stop_loss)

    # Target 1: prior 10-bar swing high (zero-lookahead: iloc[:M])
    prior_highs = high_s.iloc[:M]
    prior_10d_high = (
        float(prior_highs.iloc[-10:].max()) if len(prior_highs) >= 10
        else float(prior_highs.max())
    )
    target_1 = round(prior_10d_high, 2)
    rr_ratio = round((target_1 - entry_price) / risk_dist, 2) if risk_dist > 0 else 0.0

    bars_ago = M - best_sweep_idx
    stage3_result = ReversalV2Result(
        symbol=symbol,
        stage=ReversalStage.RECLAIM_CONFIRMED,
        variant_id="REVERSAL_V21D_FULL_FUNNEL",
        live_trade_allowed=(not is_nq_universe) and rr_ratio >= 1.5,
        engine_path="V21_STAGE3_RECLAIM",
        reasons=[
            f"V2.1 Stage2 Sweep {bars_ago} bars ago (sweep low Rs{best_sweep_low:.2f})",
            f"V2.1 Stage3 Reclaim Rs{close_m:.2f} > swept support Rs{swept_support:.2f}. "
            f"SL=Rs{stop_loss:.2f}, T1=Rs{target_1:.2f}, R:R={rr_ratio:.1f}",
        ],
        score=65.0, quality_grade="B",
        stage1_rsi=current_rsi_m,
        stage1_drop_pct=round(drop_pct_m, 1),
        stage2_sweep_vol_ratio=round(best_sweep_vol_ratio or 1.0, 2),
        stage2_sweep_low=round(sequence_low, 2),
        stage3_reclaim_close=round(close_m, 2),
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_1=target_1,
        rr_ratio=rr_ratio,
        breakout_level=round(swept_support, 2),
        is_nq_universe=is_nq_universe,
        setup_id=f"REV21_{symbol}_{date.today()}",
    )

    if is_nq_universe and cfg.nq_universe_blocks_stage5:
        stage3_result.live_trade_allowed = False
        stage3_result.reasons.append("NQ_UNIVERSE: Capped at Stage 3.")
        return stage3_result

    # ── STAGE 4: DISPLACEMENT BURST (Evaluated on Reclaim Bar) ────────────────
    candle_range_m = max(0.001, high_m - low_m)
    body_ratio_m   = abs(close_m - open_m) / candle_range_m if candle_range_m > 0 else 0.0
    close_pos_m    = (close_m - low_m)     / candle_range_m if candle_range_m > 0 else 0.5
    is_bullish_m   = close_m > open_m

    stage4_passed = (
        is_bullish_m
        and body_ratio_m >= cfg.min_displacement_body_ratio
        and close_pos_m  >= cfg.min_displacement_close_pos
    )

    if not stage4_passed:
        stage3_result.reasons.append(
            f"Stage4 pending: body={body_ratio_m:.2f} (need >={cfg.min_displacement_body_ratio:.2f}), "
            f"close_pos={close_pos_m:.2f} (need >={cfg.min_displacement_close_pos:.2f}), "
            f"bullish={is_bullish_m}"
        )
        return stage3_result

    stage4_result = ReversalV2Result(
        symbol=symbol,
        stage=ReversalStage.DISPLACEMENT_BURST,
        variant_id="REVERSAL_V21D_FULL_FUNNEL",
        live_trade_allowed=rr_ratio >= 1.5,
        engine_path="V21_STAGE4_DISPLACEMENT",
        reasons=[
            f"V2.1 Stage2 Sweep {bars_ago} bars ago (low Rs{best_sweep_low:.2f})",
            f"V2.1 Stage3 Reclaim Rs{close_m:.2f} > swept support Rs{swept_support:.2f}. "
            f"SL=Rs{stop_loss:.2f}, T1=Rs{target_1:.2f}, R:R={rr_ratio:.1f}",
            f"V2.1 Stage4 Displacement: body={body_ratio_m:.2f}, close_pos={close_pos_m:.2f}",
        ],
        score=78.0, quality_grade="B",
        stage1_rsi=current_rsi_m,
        stage1_drop_pct=round(drop_pct_m, 1),
        stage2_sweep_vol_ratio=round(best_sweep_vol_ratio or 1.0, 2),
        stage2_sweep_low=round(sequence_low, 2),
        stage3_reclaim_close=round(close_m, 2),
        stage4_body_ratio=round(body_ratio_m, 2),
        stage4_close_pos=round(close_pos_m, 2),
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_1=target_1,
        rr_ratio=rr_ratio,
        breakout_level=round(swept_support, 2),
        is_nq_universe=is_nq_universe,
        setup_id=f"REV21_{symbol}_{date.today()}",
    )

    # ── STAGE 5: HIGH-CONFIDENCE ALERT ───────────────────────────────────────
    stage5_fails = []

    # (a) Higher Low: action bar low > sweep low (structural HL vs climax bar)
    higher_low_confirmed = True
    if cfg.require_higher_low:
        higher_low_confirmed = low_m > best_sweep_low * 1.001 if best_sweep_idx < M else (
            float(df["Low"].iloc[-2]) > sequence_low * 1.001 if len(df) >= 2 else True
        )
        if not higher_low_confirmed:
            stage5_fails.append(f"Stage5 HL Fail: Reclaim low Rs{low_m:.2f} not above sweep low Rs{best_sweep_low:.2f}")

    # (b) Macro regime check
    regime_ok = macro_regime.upper() in [r.upper() for r in cfg.allowed_regimes]
    if not regime_ok:
        stage5_fails.append(f"Stage5 Regime Fail: {macro_regime} not in allowed {cfg.allowed_regimes}")

    # (c) R:R validation against first major resistance
    resistance = target_resistance
    if resistance is None:
        resistance = (
            float(df["High"].iloc[-60:].max()) if len(df) >= 60
            else float(df["High"].max())
        )

    final_rr = round((resistance - entry_price) / risk_dist, 2) if risk_dist > 0 else 0.0
    rr_ok    = final_rr >= cfg.min_rr_ratio
    if not rr_ok:
        stage5_fails.append(
            f"Stage5 RR Fail: R:R={final_rr:.1f} < minimum {cfg.min_rr_ratio:.1f} to resistance Rs{resistance:.2f}"
        )

    if stage5_fails:
        stage4_result.reasons.extend(stage5_fails)
        return stage4_result

    score = 92.0 if final_rr >= 3.5 else 85.0
    grade = "A+" if score >= 90.0 else "A"

    stage5_reasons = list(stage4_result.reasons)
    stage5_reasons.append(
        f"V2.1 Stage5 Pass: HL confirmed, regime={macro_regime}, R:R={final_rr:.1f}x to resistance Rs{resistance:.2f}"
    )

    return ReversalV2Result(
        symbol=symbol,
        stage=ReversalStage.HIGH_CONFIDENCE_ALERT,
        variant_id="REVERSAL_V21D_FULL_FUNNEL",
        live_trade_allowed=True,
        engine_path="V21_STAGE5_HIGH_CONFIDENCE",
        reasons=stage5_reasons,
        score=score,
        quality_grade=grade,
        stage1_rsi=current_rsi_m,
        stage1_drop_pct=round(drop_pct_m, 1),
        stage2_sweep_vol_ratio=round(best_sweep_vol_ratio or 1.0, 2),
        stage2_sweep_low=round(sequence_low, 2),
        stage3_reclaim_close=round(close_m, 2),
        stage4_body_ratio=round(body_ratio_m, 2),
        stage4_close_pos=round(close_pos_m, 2),
        stage5_higher_low=higher_low_confirmed,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_1=round(resistance, 2),
        rr_ratio=final_rr,
        breakout_level=round(swept_support, 2),
        is_nq_universe=is_nq_universe,
        setup_id=f"REV21_{symbol}_{date.today()}",
    )
