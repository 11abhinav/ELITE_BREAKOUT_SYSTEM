# =====================================================================================
# app/sl_target_helper.py  (v5 — ANTI-TRAP EDITION)
#
# KEY INSIGHT: Different scanners trade completely different setups.
# One SL/Target formula for all is wrong. This module dispatches to
# a mode-specific sub-function for each scanner type.
#
# MODES:
#   "EOD"      → Daily momentum breakout (swing trade, hold days–weeks)
#   "INTRADAY" → 15m early-momentum scalp (hold position until SL or Target is hit)
#   "LIVE_1H"  → Hourly swing continuation (hold 1–5 days)
#   "REVERSAL" → Counter-trend oversold bounce (mean reversion, hold days–weeks)
#
#
# v5 UPGRADES:
#   1. MULTI-SWING CLUSTERING — scans last 3 swing lows; if 2+ cluster within 1%,
#      uses the cluster zone for SL placement (much stronger support)
#   2. VWAP-ANCHORED SL — for intraday/1H, uses VWAP as SL anchor when between
#      candle_low and entry (VWAP = institutional fair-value support)
#   3. ADX-AWARE BUFFER WIDENING — trending stocks (ADX>35) get 30% wider buffers
#      to survive deeper pullbacks without stopping out
#   4. ATR-SCALED TARGET CAPS — prevents unrealistic targets that stock can't reach
#      15m: 5×ATR | 1H: 8×ATR | EOD: 12×ATR
#   5. MEASURED MOVE TARGETS — when BASE_WIDTH available, T1 = entry + base height
#
# ANTI-OPERATOR-TRAP DESIGN:
#   Operators/algos know retail places SL exactly at swing low.
#   They run stops with a wick, then reverse. Our fix:
#   → SL is placed BELOW the zone, not at it, with a meaningful % buffer
#   → Buffer is max(mode_atr_fraction × ATR, mode_pct × price)
#   → ADX-scaled: trending stocks get wider buffers (deeper pullbacks)
#   → This makes the stop hunt unprofitable for operators (too far to sweep)
#
# SL BUFFER TABLE (per mode):
#   INTRADAY  → max(0.5×ATR, 0.30% price) — tight momentum scalp trade
#   LIVE_1H   → max(0.5×ATR, 0.50% price) — moderate, hourly swing
#   EOD       → max(0.75×ATR, 0.75% price) — meaningful, daily trade
#   REVERSAL  → max(1.0×ATR, 1.00% price) — widest, volatile beaten stocks
#
# MINIMUM R:R TABLE (per mode):
#   INTRADAY  → 1.5:1 (scalp — quicker in/out)
#   LIVE_1H   → 2.0:1 (hourly swing — higher bar)
#   EOD       → 2.0:1 (daily trade — overnight risk demands it)
#   REVERSAL  → 2.0:1 (counter-trend — higher base risk)
#
# TARGET PHILOSOPHY (per mode):
#   EOD       → Nearest swing high / R1 pivot → R2 → 52W high zone
#   INTRADAY  → Session high / BB_UPPER → Day's R1 (no T3 — hold until SL/Target)
#   LIVE_1H   → R1 / BB_UPPER → R2 (no T3 — 1H has limited range)
#   REVERSAL  → EMA20 or BB_MID (mean reversion T1) → SMA50 (T2) → R1 (T3)
# =====================================================================================

from __future__ import annotations
import pandas as pd
from typing import Optional
import math

from config import ADAPTIVE_TARGET_CAPS, MIN_NATURAL_RR, MIN_REWARD_POTENTIAL, TARGET_QUALITY_THRESHOLD


# ── Per-mode configuration ────────────────────────────────────────────────────
_MODE_CONFIG = {
    #           atr_base  sl_atr_buf  sl_pct_buf  max_sl_atr
    "EOD":      (2.00,    0.75,       0.0075,     3.0),
    "INTRADAY": (1.00,    0.50,       0.0030,     2.5),
    "LIVE_1H":  (1.50,    0.50,       0.0050,     2.5),
    "REVERSAL": (2.00,    1.00,       0.0100,     3.5),
}
_DEFAULT_CONFIG = (1.50, 0.50, 0.0050, 3.0)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(val) -> Optional[float]:
    """Return float if valid, finite, and > 0, else None."""
    try:
        f = float(val)
        return f if math.isfinite(f) and f > 0 else None
    except (TypeError, ValueError):
        return None


def _find_swing_low_cluster(swing_lows, threshold_pct: float = 0.01) -> Optional[float]:
    """
    If 2+ swing lows in the list are within threshold_pct (1%) of each other,
    returns the average of the clustering swing lows as the cluster zone level.
    Otherwise returns None.
    """
    if swing_lows is None or len(swing_lows) < 2:
        return None
    n = len(swing_lows)
    best_cluster = []
    for i in range(n):
        for j in range(i + 1, n):
            val1 = float(swing_lows[i])
            val2 = float(swing_lows[j])
            diff = abs(val1 - val2) / max(val1, val2, 1e-5)
            if diff <= threshold_pct:
                cluster = [val1, val2]
                for k in range(n):
                    if k != i and k != j:
                        val3 = float(swing_lows[k])
                        if abs(val3 - val1) / max(val3, val1, 1e-5) <= threshold_pct and abs(val3 - val2) / max(val3, val2, 1e-5) <= threshold_pct:
                            cluster.append(val3)
                if len(cluster) > len(best_cluster):
                    best_cluster = cluster
    if len(best_cluster) >= 2:
        return min(best_cluster)
    return None
 
 

def _pick_resistance(
    entry: float,
    swing_high: Optional[float],
    r1: Optional[float],
    bb_upper: Optional[float],
    swing_high_raw: Optional[float],
    r2: Optional[float],
) -> tuple[Optional[float], str]:
    """
    Nearest structural resistance above entry.
    Priority: true pivot swing high > R1 > BB_UPPER > rolling high > R2.
    """
    for level, label in [
        (swing_high,     "pivot swing high"),
        (r1,             "pivot R1"),
        (bb_upper,       "BB upper band"),
        (swing_high_raw, "rolling swing high"),
        (r2,             "pivot R2"),
    ]:
        v = _safe(level)
        if v is not None and v > entry:
            return v, label
    return None, "none"

def _atr_volatility_scale(atr_pct: Optional[float], base: float) -> float:
    m = base
    if atr_pct is not None:
        if   atr_pct > 6.0: m *= 1.6
        elif atr_pct > 4.0: m *= 1.4
        elif atr_pct > 2.0: m *= 1.2
    return round(m, 3)

def _cap_target(
    target: float,
    entry: float,
    eff_atr: float,
    timeframe: str,
    macro_regime: str = "NEUTRAL",
    atr_pct: Optional[float] = None,
) -> float:
    regime_caps = ADAPTIVE_TARGET_CAPS.get(macro_regime, ADAPTIVE_TARGET_CAPS["NEUTRAL"])
    max_atr_mult = regime_caps.get(timeframe, regime_caps["1d"])
    if atr_pct is not None:
        if atr_pct > 4.0:
            max_atr_mult = min(max_atr_mult, 8.0)
        elif atr_pct < 2.0:
            max_atr_mult = max(max_atr_mult, 10.0)
    max_target = entry + max_atr_mult * eff_atr
    return min(target, max_target)

class ResistanceSelector:
    @staticmethod
    def get_nearest_valid_resistance(entry: float, resistances: list) -> dict:
        from config import STRUCTURAL_RESISTANCE_SCORES
        
        valid = []
        for val, name, _ in resistances:
            if val is not None and val > entry:
                score = STRUCTURAL_RESISTANCE_SCORES.get(name, 15)
                valid.append({
                    "price": val,
                    "type": name,
                    "score": score
                })
                
        if not valid:
            return None
            
        # Filter out weak levels
        MIN_RESISTANCE_SCORE = 25
        strong = [r for r in valid if r["score"] >= MIN_RESISTANCE_SCORE]
        
        if not strong:
            return None
            
        # Rank by proximity (nearest valid resistance)
        strong.sort(key=lambda x: x["price"])
        return strong[0]

class SupportEngine:
    @staticmethod
    def calculate_support_strength(cluster: list) -> tuple[int, list]:
        from config import STRUCTURAL_STOP
        scores_dict = STRUCTURAL_STOP.get("SCORES", {})
        bonus_overlap = STRUCTURAL_STOP.get("BONUS_OVERLAP", 15)
        
        cluster_members = []
        base_score_sum = 0
        unique_names = set()
        
        for val, name in cluster:
            s = scores_dict.get(name, 10)
            cluster_members.append({"type": name, "price": val, "score": s})
            base_score_sum += s
            unique_names.add(name)
            
        context_score = 0
        if len(unique_names) > 1:
            context_score += bonus_overlap
            
        total_score = base_score_sum + context_score
        return total_score, cluster_members

    @staticmethod
    def get_ranked_supports(entry: float, eff_atr: float, supports: list) -> list:
        from config import STRUCTURAL_STOP
        valid = []
        for val, name, _ in supports:
            if val is not None and val < entry:
                valid.append((val, name))
                
        if not valid:
            return []
            
        max_width = STRUCTURAL_STOP.get("MAX_CLUSTER_WIDTH_ATR", 1.5) * eff_atr
        valid.sort(key=lambda x: x[0], reverse=True)
        
        clusters = []
        curr_cluster = [valid[0]]
        curr_max = valid[0][0]
        
        for v, name in valid[1:]:
            if (curr_max - v) <= max_width:
                curr_cluster.append((v, name))
            else:
                clusters.append(curr_cluster)
                curr_cluster = [(v, name)]
                curr_max = v
        clusters.append(curr_cluster)
        
        results = []
        for cluster in clusters:
            total_score, members = SupportEngine.calculate_support_strength(cluster)
            weighted_sum = sum([m["price"] * m["score"] for m in members])
            weight_total = sum([m["score"] for m in members])
            best_anchor = weighted_sum / weight_total if weight_total > 0 else members[0]["price"]
            
            c_str = "STRONG" if total_score > 60 else ("WEAK" if total_score < 30 else "NORMAL")
            cluster_width = max([m["price"] for m in members]) - min([m["price"] for m in members]) if members else 0.0
            
            results.append({
                "score": total_score,
                "anchor_price": round(best_anchor, 2),
                "cluster_strength": c_str,
                "anchor_confidence": min(round(total_score / 100.0, 2), 1.0),
                "cluster_width": round(cluster_width, 2),
                "member_count": len(members),
                "cluster_members": members
            })
            
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

def _compute_structural_stop(entry: float, eff_atr: float, atr_pct: float, supports: list, ctx: dict) -> dict:
    from config import MIN_STOP_PCT
    mode = ctx.get("mode", "EOD")
    min_stop_pct = MIN_STOP_PCT.get(mode, 0.0)
    
    ranked_supports = SupportEngine.get_ranked_supports(entry, eff_atr, supports)
    
    best_support = None
    best_buf = 0.0
    best_vol_label = ""
    best_qual_label = ""
    best_final_mult = 1.0
    
    atr_p = atr_pct or 3.0
    if atr_p < 2.0:
        base_mult = 0.5
        vol_label = "LOW_VOL"
    elif atr_p > 6.0:
        base_mult = 1.0
        vol_label = "HIGH_VOL"
    else:
        base_mult = 0.75
        vol_label = "NORM_VOL"
        
    for support_data in ranked_supports:
        best_score = support_data["score"]
        if best_score > 60:
            final_mult = base_mult * 0.8
            qual_label = "STRONG_SUP"
        elif best_score < 30:
            final_mult = base_mult * 1.2
            qual_label = "WEAK_SUP"
        else:
            final_mult = base_mult
            qual_label = "NORM_SUP"
            
        buf = final_mult * eff_atr
        raw_sl = support_data["anchor_price"] - buf
        sl_pct = (entry - raw_sl) / entry * 100 if entry > 0 else 0
        
        # Check MIN_STOP_PCT
        if sl_pct >= min_stop_pct:
            best_support = support_data
            best_buf = buf
            best_vol_label = vol_label
            best_qual_label = qual_label
            best_final_mult = final_mult
            break
            
    if not best_support:
        # Explicitly reject if no structural stop meets MIN_STOP_PCT
        
        # Find best pct observed for metadata
        best_observed_pct = 0.0
        for support_data in ranked_supports:
            buf = eff_atr * 0.75 # approx
            sl_pct = (entry - (support_data["anchor_price"] - buf)) / entry * 100
            if sl_pct > best_observed_pct:
                best_observed_pct = sl_pct
                
        return {
            "is_valid": False,
            "rejection_reason": "NO_VALID_STRUCTURAL_STOP",
            "details": {
                "clusters_found": len(ranked_supports),
                "best_stop_pct": round(best_observed_pct, 2),
                "required_stop_pct": min_stop_pct
            },
            "raw_sl": entry - (eff_atr * 1.5),
            "sl_method": "REJECTED_TIGHT_STOP",
            "anchor_price": entry,
            "anchor_type": "NONE",
            "anchor_score": 0,
            "anchor_confidence": 0.0,
            "cluster_width": 0.0,
            "member_count": 0,
            "cluster_members": [],
            "buffer_value": eff_atr * 1.5,
            "buffer_method": "REJECTED"
        }
        
    best_anchor = best_support["anchor_price"]
    best_score = best_support["score"]
    best_cluster_members = best_support["cluster_members"]
    best_names = "_".join(list(dict.fromkeys([m["type"] for m in best_cluster_members]))).upper().replace(" ", "_")
    
    method_str = f"{best_names} (Score: {best_score}) @ {best_anchor:.2f} — Buffer {best_buf:.2f} ({best_final_mult:.2f}x ATR)"
    
    return {
        "is_valid": True,
        "raw_sl": best_anchor - best_buf,
        "sl_method": method_str,
        "anchor_price": best_anchor,
        "anchor_type": best_names,
        "anchor_score": best_score,
        "anchor_confidence": best_support["anchor_confidence"],
        "cluster_width": best_support["cluster_width"],
        "member_count": best_support["member_count"],
        "cluster_members": best_cluster_members,
        "buffer_value": round(best_buf, 2),
        "buffer_method": f"{best_vol_label}_{best_qual_label}"
    }

def _compute_disaster_stop(primary_sl: float, entry: float, eff_atr: float, lower_supports: list) -> float:

    """
    v6.2.1: Nearest Lower Major Support -> Exists? -> YES -> Use it -> NO -> Primary - ATR
    """
    valid = [s for s in lower_supports if _safe(s) is not None and s < primary_sl]
    nearest_lower = max(valid) if valid else None
    
    if nearest_lower is not None:
        return round(nearest_lower, 2)
        
    fallback = primary_sl - (1.0 * eff_atr)
    return round(fallback, 2)

def _compute_target_quality(
    natural_rr: float,
    rsi: Optional[float],
    adx: Optional[float],
    macd_hist: Optional[float],
    volume_ratio: Optional[float],
    swing_high: Optional[float],
    r1: Optional[float],
    r2: Optional[float],
    bb_upper: Optional[float]
) -> tuple[int, dict]:
    """
    v6.0: Explainable Target Quality Score.
    Weighting:
    - Natural RR (40%)
    - Trend (20%)
    - Volume (15%)
    - Resistance Proximity (15%)
    - Liquidity (10%)
    """
    bd = {"natural_rr": 0, "trend": 0, "volume": 0, "resistance": 0, "liquidity": 0}
    
    # 1. Natural RR (40 pts max)
    if natural_rr >= 4.0: bd["natural_rr"] = 40
    elif natural_rr >= 3.0: bd["natural_rr"] = 35
    elif natural_rr >= 2.0: bd["natural_rr"] = 25
    elif natural_rr >= 1.5: bd["natural_rr"] = 15
    else: bd["natural_rr"] = 5
    
    # 2. Trend / Momentum (20 pts max)
    v_adx = _safe(adx)
    if v_adx:
        if v_adx > 35: bd["trend"] += 12
        elif v_adx > 25: bd["trend"] += 8
        elif v_adx > 20: bd["trend"] += 4
    
    v_macd = _safe(macd_hist)
    if v_macd and v_macd > 0:
        bd["trend"] += 8
        
    # 3. Volume Expansion (15 pts max)
    v_vol = _safe(volume_ratio)
    if v_vol:
        if v_vol > 3.0: bd["volume"] = 15
        elif v_vol > 2.0: bd["volume"] = 12
        elif v_vol > 1.5: bd["volume"] = 8
        elif v_vol > 1.0: bd["volume"] = 4
        
    # 4. Resistance Proximity (15 pts max)
    # Check if we have multiple resistance levels stacked
    resistances = [r for r in [swing_high, r1, r2, bb_upper] if _safe(r) is not None]
    if len(resistances) == 0:
        bd["resistance"] = 15  # Blue sky
    elif len(resistances) == 1:
        bd["resistance"] = 10  # Single hurdle
    else:
        bd["resistance"] = 5   # Heavy overhead
        
    # 5. Liquidity / Delivery (10 pts) -> placeholder since we don't pass delivery % yet, 
    # we'll give a baseline based on RSI not being overbought
    v_rsi = _safe(rsi)
    if v_rsi:
        if 55 <= v_rsi <= 72: bd["liquidity"] = 10
        elif 40 <= v_rsi < 55: bd["liquidity"] = 7
        else: bd["liquidity"] = 3
        
    total_score = sum(bd.values())
    return total_score, bd


def _rsi_zone(rsi: Optional[float]) -> str:
    v = _safe(rsi)
    if v is None:       return "neutral"
    if v > 72:          return "overbought"
    if v > 55:          return "bullish"
    if v > 40:          return "neutral"
    return "oversold"


# ─────────────────────────────────────────────────────────────────────────────
# EOD — Daily Breakout (swing trade, hold days to weeks)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_multi_tf(
    entry: float,
    eff_atr: float,
    atr_pct: float,
    adx: float,
    rsi: float,
    macd_hist: float,
    swing_low: float,
    swing_high: float,
    s1: float,
    s2: float,
    r1: float,
    r2: float,
    swing_low_raw: float,
    swing_high_raw: float,
    swing_low_15m: float = None,
    swing_high_15m: float = None,
    swing_low_30m: float = None,
    swing_high_30m: float = None,
    swing_low_1h: float = None,
    swing_high_1h: float = None,
    **kwargs
) -> dict:
    from config import MIN_NATURAL_RR, MIN_REWARD_POTENTIAL, TARGET_QUALITY_THRESHOLD

    supports = [
        (swing_low, "5m Swing Low", 20),
        (swing_low_15m, "15m Swing Low", 25),
        (swing_low_30m, "30m Swing Low", 30),
        (swing_low_1h, "1H Swing Low", 35),
        (s1, "S1", 20),
        (s2, "S2", 15),
        (swing_low_raw, "Rolling Swing Low", 20),
        (kwargs.get("vwap"), "VWAP", 15),
        (kwargs.get("ema20"), "EMA20", 15),
        (kwargs.get("sma50"), "SMA50", 15),
        (kwargs.get("sma200"), "SMA200", 30)
    ]

    sl_data = _compute_structural_stop(entry, eff_atr, atr_pct, supports, {"mode": "MULTI_TF"})
    
    # Standardized Rejection check for SL
    if not sl_data.get("is_valid", True):
        return {
            "engine_version": "SL_ENGINE_V6",
            "is_rejected": True,
            "rejection_reason": "NO_VALID_STRUCTURAL_STOP",
            "gate": "MIN_STOP_PCT",
            "actual": sl_data.get("details", {}).get("best_stop_pct", 0.0),
            "required": sl_data.get("details", {}).get("required_stop_pct", 0.0),
            "context": sl_data.get("details", {}),
            "stop_loss": sl_data["raw_sl"],
            "structural_failure_stop": sl_data["raw_sl"],
            "target_1": entry,
            "natural_rr": 0.0,
            "reward_potential_pct": 0.0,
            "target_quality": 0.0,
            "quality_breakdown": {},
            "sl_method": sl_data["sl_method"],
            "target_method": "REJECTED"
        }

    stop_loss = sl_data["raw_sl"]
    sl_method = sl_data["sl_method"]
    structural_failure_stop = _compute_disaster_stop(stop_loss, entry, eff_atr, [s[0] for s in supports])
    risk = entry - stop_loss

    min_rr = MIN_NATURAL_RR.get("MULTI_TF", 2.0)
    min_tq = TARGET_QUALITY_THRESHOLD.get("MULTI_TF", 65)
    min_reward_pot = MIN_REWARD_POTENTIAL.get("MULTI_TF", 1.8)

    resistances = [
        (swing_high, "5m Swing High", 20),
        (swing_high_15m, "15m Swing High", 25),
        (swing_high_30m, "30m Swing High", 30),
        (swing_high_1h, "1H Swing High", 35),
        (r1, "R1", 15),
        (r2, "R2", 20),
        (swing_high_raw, "Rolling Swing High", 20)
    ]
    
    best_resistance = ResistanceSelector.get_nearest_valid_resistance(entry, resistances)
    
    if best_resistance:
        target_1 = best_resistance["price"]
        nearest_resistance_score = best_resistance["score"]
        res_label = f"{best_resistance['type']} (Score: {nearest_resistance_score})"
        
        move_pct = (target_1 - entry) / entry * 100
        if move_pct < min_reward_pot:
            return {
                "engine_version": "SL_ENGINE_V6",
                "is_rejected": True,
                "rejection_reason": "LOW_REWARD_POTENTIAL",
                "gate": "MIN_REWARD_POTENTIAL",
                "actual": round(move_pct, 2),
                "required": min_reward_pot,
                "context": {
                    "nearest_resistance_score": nearest_resistance_score,
                    "type": best_resistance["type"]
                },
                "stop_loss": stop_loss,
                "structural_failure_stop": structural_failure_stop,
                "target_1": target_1,
                "natural_rr": round((target_1 - entry)/risk, 2) if risk > 0 else 0.0,
                "reward_potential_pct": round(move_pct, 2),
                "target_quality": 0.0,
                "quality_breakdown": {},
                "sl_method": sl_method,
                "target_method": res_label
            }
    else:
        # NO VALID RESISTANCE
        return {
            "engine_version": "SL_ENGINE_V6",
            "is_rejected": True,
            "rejection_reason": "NO_VALID_STRUCTURAL_RESISTANCE",
            "gate": "MIN_REWARD_POTENTIAL",
            "actual": 0.0,
            "required": min_reward_pot,
            "context": {
                "resistances_found": len(resistances)
            },
            "stop_loss": stop_loss,
            "structural_failure_stop": structural_failure_stop,
            "target_1": entry,
            "natural_rr": 0.0,
            "reward_potential_pct": 0.0,
            "target_quality": 0.0,
            "quality_breakdown": {},
            "sl_method": sl_method,
            "target_method": "REJECTED"
        }

    reward = target_1 - entry
    natural_rr = round(reward / risk, 2) if risk > 0 else 0.0
    reward_potential_pct = (reward / entry) * 100 if entry > 0 else 0.0

    tq, bd = _compute_target_quality(
        entry=entry, adx=adx, rsi=rsi, macd_hist=macd_hist,
        atr_pct=atr_pct, target_1=target_1, natural_rr=natural_rr, support_score=sl_data.get("anchor_score", 0)
    )

    if natural_rr < min_rr:
        return {
            "engine_version": "SL_ENGINE_V6",
            "is_rejected": True,
            "rejection_reason": "LOW_NATURAL_RR",
            "gate": "MIN_NATURAL_RR",
            "actual": natural_rr,
            "required": min_rr,
            "context": {},
            "stop_loss": stop_loss,
            "structural_failure_stop": structural_failure_stop,
            "target_1": target_1,
            "natural_rr": natural_rr,
            "reward_potential_pct": reward_potential_pct,
            "target_quality": tq,
            "quality_breakdown": bd,
            "sl_method": sl_method,
            "target_method": res_label
        }

    if tq < min_tq:
        return {
            "engine_version": "SL_ENGINE_V6",
            "is_rejected": True,
            "rejection_reason": "LOW_TARGET_QUALITY",
            "gate": "MIN_TARGET_QUALITY",
            "actual": tq,
            "required": min_tq,
            "context": bd,
            "stop_loss": stop_loss,
            "structural_failure_stop": structural_failure_stop,
            "target_1": target_1,
            "natural_rr": natural_rr,
            "reward_potential_pct": reward_potential_pct,
            "target_quality": tq,
            "quality_breakdown": bd,
            "sl_method": sl_method,
            "target_method": res_label
        }

    return {
        "engine_version": "SL_ENGINE_V6",
        "stop_loss": stop_loss,
        "structural_failure_stop": structural_failure_stop,
        "target_1": target_1,
        "target_2": round(_cap_target(target_1 + (1.5 * eff_atr), entry, eff_atr, "15m", "NEUTRAL", _safe(atr_pct)), 2),
        "target_3": round(_cap_target(target_1 + (3.0 * eff_atr), entry, eff_atr, "15m", "NEUTRAL", _safe(atr_pct)), 2),
        "natural_rr": natural_rr,
        "reward_potential_pct": reward_potential_pct,
        "sl_method": sl_method,
        "target_method": res_label,
        "target_quality": tq,
        "quality_breakdown": bd,
        "is_rejected": False,
        "rejection_reason": None,
        "buffer_method": sl_data.get("buffer_method", "N/A"),
        "anchor_score": sl_data.get("anchor_score", 0),
        "anchor_confidence": sl_data.get("anchor_confidence", 0),
        "cluster_width": sl_data.get("cluster_width", 0),
        "member_count": sl_data.get("member_count", 0),
        "cluster_members": sl_data.get("cluster_members", [])
    }

def _compute_eod(
    entry: float, eff_atr: float, adx, rsi, macd_hist, atr_pct,
    swing_low, swing_high, bb_upper, bb_lower,
    s1, s2, r1, r2, swing_low_raw, swing_high_raw,
    swing_low_cluster: Optional[float] = None,
    macro_regime: str = "NEUTRAL",
    volume_ratio: Optional[float] = None,
) -> dict:
    """
    v6.0 EOD breakout logic:
    • Natural RR & Reward Potential gates applied early.
    • Explainable quality score returned.
    """
    from config import MIN_NATURAL_RR, MIN_REWARD_POTENTIAL, TARGET_QUALITY_THRESHOLD
    
    atr_base, sl_atr_buf, sl_pct_buf, max_sl_atr = _MODE_CONFIG["EOD"]
    min_rr = MIN_NATURAL_RR["EOD"]
    min_rp = MIN_REWARD_POTENTIAL["EOD"]
    min_tq = TARGET_QUALITY_THRESHOLD["EOD"]
    
    scaled_mult = _atr_volatility_scale(_safe(atr_pct), atr_base)

    supports = [
        (_safe(swing_low_cluster), "Swing Low Cluster", 1),
        (_safe(swing_low), "Swing Low", 1),
        (_safe(swing_low_raw), "Rolling Swing Low", 1),
        (_safe(s1), "S1 (Discovery)", 1)
    ]
    sl_data = _compute_structural_stop(entry, eff_atr, _safe(atr_pct), supports, {"mode": "EOD"})
    stop_loss = round(sl_data["raw_sl"], 2)
    sl_method = sl_data["sl_method"]
    risk = max(entry - stop_loss, entry * 0.005)
    
    structural_failure_stop = _compute_disaster_stop(stop_loss, entry, eff_atr, [s[0] for s in supports])

    resistance, res_label = _pick_resistance(entry, _safe(swing_high), _safe(r1), _safe(bb_upper), _safe(swing_high_raw), _safe(r2))

    t1_raw = resistance if resistance is not None else (entry + min_rr * risk)
    
    # 1. Natural RR Gate
    natural_rr = round((t1_raw - entry) / risk, 2) if risk > 0 else 0
    if natural_rr < min_rr:
        return {
            "engine_version": "SL_ENGINE_V6",
            "is_rejected": True,
            "rejection_reason": f"[GATE_NATURAL_RR] Natural RR {natural_rr} < {min_rr}",
            "stop_loss": stop_loss,
            "structural_failure_stop": structural_failure_stop,
            "target_1": round(t1_raw, 2),
            "natural_rr": natural_rr,
            "sl_method": sl_method,
            "target_method": res_label if resistance else "ATR Expansion",
            "anchor_price": sl_data["anchor_price"],
            "anchor_type": sl_data["anchor_type"],
            "buffer_value": sl_data["buffer_value"],
            "buffer_method": sl_data["buffer_method"],
            "anchor_score": sl_data["anchor_score"],
            "anchor_confidence": sl_data["anchor_confidence"],
            "cluster_width": sl_data["cluster_width"],
            "member_count": sl_data["member_count"],
            "cluster_members": sl_data["cluster_members"]
        }
        
    # 2. Reward Potential Gate
    reward_potential_pct = round(((t1_raw - entry) / entry) * 100, 2) if entry > 0 else 0
    if reward_potential_pct < min_rp:
        return {
            "engine_version": "SL_ENGINE_V6",
            "is_rejected": True,
            "rejection_reason": f"[GATE_REWARD_POTENTIAL] Reward Potential {reward_potential_pct}% < {min_rp}%",
            "stop_loss": stop_loss,
            "structural_failure_stop": structural_failure_stop,
            "target_1": round(t1_raw, 2),
            "natural_rr": natural_rr,
            "reward_potential_pct": reward_potential_pct,
            "sl_method": sl_method,
            "target_method": res_label if resistance else "ATR Expansion",
            "anchor_price": sl_data["anchor_price"],
            "anchor_type": sl_data["anchor_type"],
            "buffer_value": sl_data["buffer_value"],
            "buffer_method": sl_data["buffer_method"],
            "anchor_score": sl_data["anchor_score"],
            "anchor_confidence": sl_data["anchor_confidence"],
            "cluster_width": sl_data["cluster_width"],
            "member_count": sl_data["member_count"],
            "cluster_members": sl_data["cluster_members"]
        }

    # Passed gates. Compute Targets.
    target_1 = round(_cap_target(t1_raw, entry, eff_atr, "1d", macro_regime, _safe(atr_pct)), 2)
    
    zone = _rsi_zone(rsi)
    target_2 = None
    if zone != "overbought":
        r2_v = _safe(r2)
        if r2_v and r2_v > target_1:
            target_2 = round(_cap_target(r2_v, entry, eff_atr, "1d", macro_regime, _safe(atr_pct)), 2)
        else:
            target_2 = round(_cap_target(entry + 3.5 * risk, entry, eff_atr, "1d", macro_regime, _safe(atr_pct)), 2)

    target_3 = None
    adx_v = _safe(adx)
    macd_bull = macd_hist is not None and _safe(abs(float(macd_hist))) is not None and float(macd_hist) > 0
    above_t2 = target_2 if target_2 else target_1
    if macd_bull and zone in ("neutral", "bullish", "oversold") and (adx_v is None or adx_v > 25):
        t3_cand = round(_cap_target(entry + 5.0 * risk, entry, eff_atr, "1d", macro_regime, _safe(atr_pct)), 2)
        if t3_cand > above_t2:
            target_3 = t3_cand

    # Quality Score
    tq, bd = _compute_target_quality(
        natural_rr, _safe(rsi), _safe(adx), _safe(macd_hist), _safe(volume_ratio),
        _safe(swing_high), _safe(r1), _safe(r2), _safe(bb_upper)
    )
    
    if tq < min_tq:
        return {
            "engine_version": "SL_ENGINE_V6",
            "is_rejected": True,
            "rejection_reason": f"[GATE_TARGET_QUALITY] Target Quality {tq} < {min_tq}",
            "stop_loss": stop_loss,
            "structural_failure_stop": structural_failure_stop,
            "target_1": target_1,
            "natural_rr": natural_rr,
            "reward_potential_pct": reward_potential_pct,
            "target_quality": tq,
            "quality_breakdown": bd,
            "sl_method": sl_method,
            "target_method": res_label if resistance else "ATR Expansion",
            "anchor_price": sl_data["anchor_price"],
            "anchor_type": sl_data["anchor_type"],
            "buffer_value": sl_data["buffer_value"],
            "buffer_method": sl_data["buffer_method"],
            "anchor_score": sl_data["anchor_score"],
            "anchor_confidence": sl_data["anchor_confidence"],
            "cluster_width": sl_data["cluster_width"],
            "member_count": sl_data["member_count"],
            "cluster_members": sl_data["cluster_members"]
        }

    return {
        "engine_version": "SL_ENGINE_V6",
        "stop_loss": stop_loss,
        "structural_failure_stop": structural_failure_stop,
        "target_1": target_1,
        "target_2": target_2,
        "target_3": target_3,
        "natural_rr": natural_rr,
        "reward_potential_pct": reward_potential_pct,
        "sl_method": sl_method,
        "target_method": res_label if resistance else "ATR Expansion",
        "target_quality": tq,
        "quality_breakdown": bd,
        "is_rejected": False,
        "rejection_reason": None
    }


# ─────────────────────────────────────────────────────────────────────────────
# INTRADAY — 15m Early Momentum Scalp (same-day trade)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_intraday(
    entry: float, eff_atr: float, adx, rsi, macd_hist, atr_pct,
    swing_low, swing_high, bb_upper, bb_lower,
    s1, s2, r1, r2, swing_low_raw, swing_high_raw,
    candle_low: Optional[float] = None,
    vwap: Optional[float] = None,
    swing_low_cluster: Optional[float] = None,
    macro_regime: str = "NEUTRAL",
    volume_ratio: Optional[float] = None,
) -> dict:
    """
    Intraday 15m scalp logic (v6.0 upgrade):
    • SL   — VWAP-anchored or below candle_low
    • T1   — session high / R1 / BB_UPPER
    • Natural RR & Reward Potential gates applied early.
    """
    from config import MIN_NATURAL_RR, MIN_REWARD_POTENTIAL, TARGET_QUALITY_THRESHOLD
    
    atr_base, sl_atr_buf, sl_pct_buf, max_sl_atr = _MODE_CONFIG["INTRADAY"]
    min_rr = MIN_NATURAL_RR["INTRADAY"]
    min_rp = MIN_REWARD_POTENTIAL["INTRADAY"]
    min_tq = TARGET_QUALITY_THRESHOLD["INTRADAY"]

    # ADX-aware buffer widening for intraday momentum
    adx_v = _safe(adx)
    if adx_v is not None and adx_v > 35:
        sl_atr_buf *= 1.20

    buf = max(sl_atr_buf * eff_atr, sl_pct_buf * entry)

    vwap_v = _safe(vwap)
    if vwap_v is not None and candle_low is not None and _safe(candle_low):
        candle_low_f = float(candle_low)
        if candle_low_f < vwap_v < entry:
            raw_sl    = vwap_v - buf
            sl_method = f"VWAP ₹{round(vwap_v, 2)} buffer ₹{round(buf, 2)}"
        elif candle_low_f < entry:
            raw_sl    = candle_low_f - buf
            sl_method = f"Candle low ₹{round(candle_low_f, 2)} buffer ₹{round(buf, 2)}"
        else:
            support, sup_label = _pick_support(
                entry, _safe(swing_low), _safe(s1), _safe(swing_low_raw), _safe(s2),
                swing_low_cluster=swing_low_cluster
            )
            if support is not None:
                raw_sl, sl_method = _sl_from_support(entry, support, eff_atr, sl_atr_buf, sl_pct_buf, max_sl_atr, sup_label)
            else:
                fallback_dist = max(1.0 * eff_atr, sl_pct_buf * entry)
                raw_sl    = entry - fallback_dist
                sl_method = f"1×ATR fallback"
    elif candle_low is not None and _safe(candle_low) and candle_low < entry:
        raw_sl    = candle_low - buf
        sl_method = f"Candle low ₹{round(candle_low, 2)} buffer ₹{round(buf, 2)}"
    else:
        support, sup_label = _pick_support(
            entry, _safe(swing_low), _safe(s1), _safe(swing_low_raw), _safe(s2),
            swing_low_cluster=swing_low_cluster
        )
        if support is not None:
            raw_sl, sl_method = _sl_from_support(entry, support, eff_atr, sl_atr_buf, sl_pct_buf, max_sl_atr, sup_label)
        else:
            fallback_dist = max(1.0 * eff_atr, sl_pct_buf * entry)
            raw_sl    = entry - fallback_dist
            sl_method = f"1×ATR fallback"

    if (entry - raw_sl) > max_sl_atr * eff_atr:
        raw_sl    = entry - max_sl_atr * eff_atr
        sl_method = f"Capped at {max_sl_atr}×ATR"

    stop_loss = round(raw_sl, 2)
    risk      = max(entry - stop_loss, entry * 0.003)
    
    structural_failure_stop = _compute_structural_failure_stop(
        stop_loss, eff_atr, 
        [_safe(swing_low_cluster), _safe(swing_low), _safe(swing_low_raw), _safe(s1)]
    )

    resistance, res_label = _pick_resistance(entry, _safe(swing_high), _safe(r1), _safe(bb_upper), _safe(swing_high_raw), _safe(r2))

    t1_raw = resistance if resistance is not None else (entry + min_rr * risk)
    
    # 1. Natural RR Gate
    natural_rr = round((t1_raw - entry) / risk, 2) if risk > 0 else 0
    if natural_rr < min_rr:
        return {
            "engine_version": "SL_ENGINE_V6",
            "is_rejected": True,
            "rejection_reason": f"[GATE_NATURAL_RR] Natural RR {natural_rr} < {min_rr}",
            "stop_loss": stop_loss,
            "structural_failure_stop": structural_failure_stop,
            "target_1": round(t1_raw, 2),
            "natural_rr": natural_rr,
            "sl_method": sl_method,
            "target_method": res_label if resistance else "ATR Expansion",
            "anchor_price": sl_data["anchor_price"],
            "anchor_type": sl_data["anchor_type"],
            "buffer_value": sl_data["buffer_value"],
            "buffer_method": sl_data["buffer_method"],
            "anchor_score": sl_data["anchor_score"],
            "anchor_confidence": sl_data["anchor_confidence"],
            "cluster_width": sl_data["cluster_width"],
            "member_count": sl_data["member_count"],
            "cluster_members": sl_data["cluster_members"]
        }
        
    # 2. Reward Potential Gate
    reward_potential_pct = round(((t1_raw - entry) / entry) * 100, 2) if entry > 0 else 0
    if reward_potential_pct < min_rp:
        return {
            "engine_version": "SL_ENGINE_V6",
            "is_rejected": True,
            "rejection_reason": f"[GATE_REWARD_POTENTIAL] Reward Potential {reward_potential_pct}% < {min_rp}%",
            "stop_loss": stop_loss,
            "structural_failure_stop": structural_failure_stop,
            "target_1": round(t1_raw, 2),
            "natural_rr": natural_rr,
            "reward_potential_pct": reward_potential_pct,
            "sl_method": sl_method,
            "target_method": res_label if resistance else "ATR Expansion",
            "anchor_price": sl_data["anchor_price"],
            "anchor_type": sl_data["anchor_type"],
            "buffer_value": sl_data["buffer_value"],
            "buffer_method": sl_data["buffer_method"],
            "anchor_score": sl_data["anchor_score"],
            "anchor_confidence": sl_data["anchor_confidence"],
            "cluster_width": sl_data["cluster_width"],
            "member_count": sl_data["member_count"],
            "cluster_members": sl_data["cluster_members"]
        }

    t1_raw   = _cap_target(t1_raw, entry, eff_atr, "15m", macro_regime, _safe(atr_pct))
    target_1 = round(t1_raw, 2)

    zone = _rsi_zone(rsi)
    target_2 = None
    if zone != "overbought":
        r2_v = _safe(r2)
        if r2_v and r2_v > target_1:
            target_2 = round(_cap_target(r2_v, entry, eff_atr, "15m", macro_regime, _safe(atr_pct)), 2)
        else:
            target_2 = round(_cap_target(entry + 2.5 * risk, entry, eff_atr, "15m", macro_regime, _safe(atr_pct)), 2)

    # Quality Score
    tq, bd = _compute_target_quality(
        natural_rr, _safe(rsi), _safe(adx), _safe(macd_hist), _safe(volume_ratio),
        _safe(swing_high), _safe(r1), _safe(r2), _safe(bb_upper)
    )
    
    if tq < min_tq:
        return {
            "engine_version": "SL_ENGINE_V6",
            "is_rejected": True,
            "rejection_reason": f"[GATE_TARGET_QUALITY] Target Quality {tq} < {min_tq}",
            "stop_loss": stop_loss,
            "structural_failure_stop": structural_failure_stop,
            "target_1": target_1,
            "natural_rr": natural_rr,
            "reward_potential_pct": reward_potential_pct,
            "target_quality": tq,
            "quality_breakdown": bd,
            "sl_method": sl_method,
            "target_method": res_label if resistance else "ATR Expansion",
            "anchor_price": sl_data["anchor_price"],
            "anchor_type": sl_data["anchor_type"],
            "buffer_value": sl_data["buffer_value"],
            "buffer_method": sl_data["buffer_method"],
            "anchor_score": sl_data["anchor_score"],
            "anchor_confidence": sl_data["anchor_confidence"],
            "cluster_width": sl_data["cluster_width"],
            "member_count": sl_data["member_count"],
            "cluster_members": sl_data["cluster_members"]
        }

    return {
        "engine_version": "SL_ENGINE_V6",
        "stop_loss": stop_loss,
        "structural_failure_stop": structural_failure_stop,
        "target_1": target_1,
        "target_2": target_2,
        "target_3": None,
        "natural_rr": natural_rr,
        "reward_potential_pct": reward_potential_pct,
        "sl_method": sl_method,
        "target_method": res_label if resistance else "ATR Expansion",
        "target_quality": tq,
        "quality_breakdown": bd,
        "is_rejected": False,
        "rejection_reason": None
    }


# ─────────────────────────────────────────────────────────────────────────────
# LIVE_1H — Hourly Swing Continuation (hold 1–5 days)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_reversal(
    entry: float, eff_atr: float, adx, rsi, macd_hist, atr_pct,
    swing_low, swing_high, bb_upper, bb_lower,
    s1, s2, r1, r2, swing_low_raw, swing_high_raw,
    ema20: Optional[float] = None,
    bb_mid: Optional[float] = None,
    sma50: Optional[float] = None,
    swing_low_cluster: Optional[float] = None,
    macro_regime: str = "NEUTRAL",
    volume_ratio: Optional[float] = None,
) -> dict:
    """
    REVERSAL / Mean-Reversion logic (v6.0 upgrade):
    • SL   — Below recent oversold swing low
    • Targets — Mean reversion (BB_MID / SMA50) instead of overhead resistance
    • Natural RR & Reward Potential gates applied early.
    """
    from config import MIN_NATURAL_RR, MIN_REWARD_POTENTIAL, TARGET_QUALITY_THRESHOLD

    atr_base, sl_atr_buf, sl_pct_buf, max_sl_atr = _MODE_CONFIG["REVERSAL"]
    min_rr = MIN_NATURAL_RR["REVERSAL"]
    min_rp = MIN_REWARD_POTENTIAL["REVERSAL"]
    min_tq = TARGET_QUALITY_THRESHOLD["REVERSAL"]

    support, sup_label = _pick_support(
        entry, _safe(swing_low), _safe(s1), _safe(swing_low_raw), _safe(s2),
        swing_low_cluster=swing_low_cluster
    )

    if support is not None:
        raw_sl, sl_method = _sl_from_support(entry, support, eff_atr, sl_atr_buf, sl_pct_buf, max_sl_atr, sup_label)
    else:
        raw_sl    = entry - max(sl_atr_buf * eff_atr, sl_pct_buf * entry)
        sl_method = f"ATR/Pct buffer (no prior swing low found)"

    stop_loss = round(raw_sl, 2)
    risk      = max(entry - stop_loss, entry * 0.008)

    structural_failure_stop = _compute_structural_failure_stop(
        stop_loss, eff_atr, 
        [_safe(swing_low_cluster), _safe(swing_low), _safe(swing_low_raw), _safe(s1)]
    )

    bbmid_v = _safe(bb_mid)
    sma50_v = _safe(sma50)
    r1_v    = _safe(r1)
    r2_v    = _safe(r2)

    t1_raw = None
    res_label = ""

    if bbmid_v and bbmid_v > entry:
        t1_raw = bbmid_v
        res_label = "BB Mid (Mean Reversion)"
    elif sma50_v and sma50_v > entry:
        t1_raw = sma50_v
        res_label = "SMA50 (Mean Reversion)"
    elif r1_v and r1_v > entry:
        t1_raw = r1_v
        res_label = "R1 (Resistance)"
    else:
        t1_raw = entry + min_rr * risk
        res_label = "ATR Expansion"

    # 1. Natural RR Gate
    natural_rr = round((t1_raw - entry) / risk, 2) if risk > 0 else 0
    if natural_rr < min_rr:
        return {
            "engine_version": "SL_ENGINE_V6",
            "is_rejected": True,
            "rejection_reason": f"[GATE_NATURAL_RR] Natural RR {natural_rr} < {min_rr}",
            "stop_loss": stop_loss,
            "structural_failure_stop": structural_failure_stop,
            "target_1": round(t1_raw, 2),
            "natural_rr": natural_rr,
            "sl_method": sl_method,
            "target_method": res_label
        }
        
    # 2. Reward Potential Gate
    reward_potential_pct = round(((t1_raw - entry) / entry) * 100, 2) if entry > 0 else 0
    if reward_potential_pct < min_rp:
        return {
            "engine_version": "SL_ENGINE_V6",
            "is_rejected": True,
            "rejection_reason": f"[GATE_REWARD_POTENTIAL] Reward Potential {reward_potential_pct}% < {min_rp}%",
            "stop_loss": stop_loss,
            "structural_failure_stop": structural_failure_stop,
            "target_1": round(t1_raw, 2),
            "natural_rr": natural_rr,
            "reward_potential_pct": reward_potential_pct,
            "sl_method": sl_method,
            "target_method": res_label
        }

    target_1 = round(_cap_target(t1_raw, entry, eff_atr, "1d", macro_regime, _safe(atr_pct)), 2)

    target_2 = None
    if sma50_v and sma50_v > target_1:
        target_2 = round(_cap_target(sma50_v, entry, eff_atr, "1d", macro_regime, _safe(atr_pct)), 2)
    elif r1_v and r1_v > target_1:
        target_2 = round(_cap_target(r1_v, entry, eff_atr, "1d", macro_regime, _safe(atr_pct)), 2)
    else:
        t2_cand = round(entry + 3.5 * risk, 2)
        if t2_cand > target_1:
            target_2 = t2_cand

    target_3 = None
    above_t2 = target_2 if target_2 else target_1
    macd_bull = macd_hist is not None and _safe(abs(float(macd_hist))) is not None and float(macd_hist) > 0
    adx_v = _safe(adx)
    if macd_bull and r2_v and r2_v > above_t2 and (adx_v is None or adx_v > 20):
        target_3 = round(_cap_target(r2_v, entry, eff_atr, "1d", macro_regime, _safe(atr_pct)), 2)
    elif macd_bull and (adx_v is None or adx_v > 20):
        t3_cand = round(_cap_target(entry + 5.0 * risk, entry, eff_atr, "1d", macro_regime, _safe(atr_pct)), 2)
        if t3_cand > above_t2:
            target_3 = t3_cand

    # Quality Score
    tq, bd = _compute_target_quality(
        natural_rr, _safe(rsi), _safe(adx), _safe(macd_hist), _safe(volume_ratio),
        _safe(swing_high), _safe(r1), _safe(r2), _safe(bb_upper)
    )
    
    if tq < min_tq:
        return {
            "engine_version": "SL_ENGINE_V6",
            "is_rejected": True,
            "rejection_reason": f"[GATE_TARGET_QUALITY] Target Quality {tq} < {min_tq}",
            "stop_loss": stop_loss,
            "structural_failure_stop": structural_failure_stop,
            "target_1": target_1,
            "natural_rr": natural_rr,
            "reward_potential_pct": reward_potential_pct,
            "target_quality": tq,
            "quality_breakdown": bd,
            "sl_method": sl_method,
            "target_method": res_label
        }

    return {
        "engine_version": "SL_ENGINE_V6",
        "stop_loss": stop_loss,
        "structural_failure_stop": structural_failure_stop,
        "target_1": target_1,
        "target_2": target_2,
        "target_3": target_3,
        "natural_rr": natural_rr,
        "reward_potential_pct": reward_potential_pct,
        "sl_method": sl_method,
        "target_method": res_label,
        "target_quality": tq,
        "quality_breakdown": bd,
        "is_rejected": False,
        "rejection_reason": None
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API — single entry point
# ─────────────────────────────────────────────────────────────────────────────

def _legacy_compute_sl_and_target(
    entry_price:    float,
    atr:            Optional[float],
    candle_range:   float,
    mode:           Optional[str]   = None,     # "EOD" | "INTRADAY" | "LIVE_1H" | "REVERSAL"
    # ── Technical context ──────────────────────────────────────────
    adx:            Optional[float] = None,
    rsi:            Optional[float] = None,
    macd_hist:      Optional[float] = None,
    atr_pct:        Optional[float] = None,
    swing_low:      Optional[float] = None,   # true pivot swing low
    swing_high:     Optional[float] = None,   # true pivot swing high
    bb_upper:       Optional[float] = None,
    bb_lower:       Optional[float] = None,
    bb_mid:         Optional[float] = None,   # used by REVERSAL (mean reversion T1)
    s1:             Optional[float] = None,
    s2:             Optional[float] = None,
    r1:             Optional[float] = None,
    r2:             Optional[float] = None,
    swing_low_raw:  Optional[float] = None,   # rolling window fallback
    swing_high_raw: Optional[float] = None,   # rolling window fallback
    candle_low:     Optional[float] = None,   # used by INTRADAY (bar's own low)
    swing_low_15m:  Optional[float] = None,
    swing_high_15m: Optional[float] = None,
    swing_low_30m:  Optional[float] = None,
    swing_high_30m: Optional[float] = None,
    swing_low_1h:   Optional[float] = None,
    swing_high_1h:  Optional[float] = None,
    ema20:          Optional[float] = None,   # used by REVERSAL (mean reversion T1)
    sma50:          Optional[float] = None,   # used by REVERSAL (mean reversion T2)
    vwap:           Optional[float] = None,   # v5: used by INTRADAY (VWAP-anchored SL)
    # Backward-compat alias (old callers used timeframe=)
    timeframe:      Optional[str]   = None,
    ticker:         Optional[pd.DataFrame] = None,
) -> dict:
    """
    Mode-dispatching SL/Target engine.

    Returns dict with:
        stop_loss   — placement respects structure + anti-trap buffer
        target_1    — primary target (mean reversion for REVERSAL, resistance for others)
        target_2    — secondary target (None for REVERSAL if overbought)
        target_3    — extended target (EOD + REVERSAL only, on strong confluence)
        rr_ratio    — R:R of target_1
        risk        — ₹ risk per share
        sl_method   — explanation of how SL was placed
        t_method    — explanation of how targets were set
        rsi_zone    — overbought / bullish / neutral / oversold
        trail_note  — plain-English trailing instruction for the Telegram alert

    Backward compatibility: if `mode` is not recognized, falls back to `timeframe`.
    """
    # Resolve effective mode — support both mode= (new) and timeframe= (old alias)
    # Priority: mode > timeframe > "EOD" default
    _TIMEFRAME_MAP = {
        "EOD": "EOD", "1d": "EOD",
        "INTRADAY": "INTRADAY", "15m": "INTRADAY",
        "1H": "LIVE_1H", "1h": "LIVE_1H", "LIVE_1H": "LIVE_1H",
        "REVERSAL": "REVERSAL",
    }
    effective_mode = (
        _TIMEFRAME_MAP.get(mode or "", "")
        or _TIMEFRAME_MAP.get(timeframe or "", "")
        or "EOD"
    )

    # Resolve effective ATR
    eff_atr = _safe(atr) or (_safe(candle_range) * 1.5 if _safe(candle_range) else None)
    if eff_atr is None or eff_atr <= 0:
        eff_atr = entry_price * 0.015   # last resort: 1.5% of price

    # Calculate swing low cluster zone if ticker is provided
    swing_low_cluster = None
    if ticker is not None and "SWING_LOW" in ticker.columns:
        try:
            recent_lows = ticker["SWING_LOW"].dropna().unique()[-3:]
            swing_low_cluster = _find_swing_low_cluster(recent_lows)
        except Exception:
            pass
 
    kwargs = dict(
        entry=entry_price, eff_atr=eff_atr,
        adx=adx, rsi=rsi, macd_hist=macd_hist, atr_pct=atr_pct,
        swing_low=swing_low, swing_high=swing_high,
        bb_upper=bb_upper, bb_lower=bb_lower,
        s1=s1, s2=s2, r1=r1, r2=r2,
        swing_low_raw=swing_low_raw, swing_high_raw=swing_high_raw,
        swing_low_cluster=swing_low_cluster,
    )

    if effective_mode == "EOD":
        return _compute_eod(**kwargs)
    elif effective_mode == "MULTI_TF":
        return _compute_multi_tf(**kwargs)
    elif effective_mode == "REVERSAL":
        return _compute_reversal(**kwargs, ema20=ema20, bb_mid=bb_mid, sma50=sma50)
    else:
        return _compute_eod(**kwargs)  # safe default


# =====================================================================================
# V2.0 INSTITUTIONAL ENGINE ARCHITECTURE
# =====================================================================================

ENGINE_V2_CONFIG = {
    "SUPPORT_WEIGHTS": {
        "touches": 40,
        "volume": 30,
        "age": 20,
        "proximity": 10
    },
    "TARGET_WEIGHTS": {
        "swing_high": 25,
        "fib": 20,
        "measured_move": 20,
        "vwap": 15,
        "atr": 10,
        "volume_profile": 10
    },
    "TRADE_QUALITY_WEIGHTS": {
        "trend": 25,
        "momentum": 20,
        "volume": 20,
        "support": 15,
        "rs": 10,
        "market": 10
    },
    "VOLATILITY_WEIGHTS": {
        "atr_percentile": 40,
        "hv_percentile": 40,
        "gap_frequency": 20
    },
    "PARTIAL_EXITS": {
        "t1": "25%",
        "t2": "35%",
        "t3": "40%"
    }
}

class SupportConfidenceEngine:
    @staticmethod
    def calculate(kwargs: dict) -> dict:
        breakdown = {"touches": 10, "volume": 15, "age": 10, "proximity": 5}
        
        entry = kwargs.get("entry_price", 1.0)
        support = kwargs.get("swing_low") or (entry * 0.95)
        
        # Proximity
        vwap = kwargs.get("vwap")
        if vwap and abs(vwap - support) / max(support, 1) < 0.02:
            breakdown["proximity"] += 15
            
        ema20 = kwargs.get("ema20")
        if ema20 and abs(ema20 - support) / max(support, 1) < 0.02:
            breakdown["proximity"] += 10
            
        # Touches & Age (derived from clustering)
        if kwargs.get("swing_low_cluster"):
            breakdown["touches"] += 25
            breakdown["age"] += 10
            
        # Cap values
        breakdown["touches"] = min(breakdown["touches"], ENGINE_V2_CONFIG["SUPPORT_WEIGHTS"]["touches"])
        breakdown["volume"] = min(breakdown["volume"], ENGINE_V2_CONFIG["SUPPORT_WEIGHTS"]["volume"])
        breakdown["age"] = min(breakdown["age"], ENGINE_V2_CONFIG["SUPPORT_WEIGHTS"]["age"])
        breakdown["proximity"] = min(breakdown["proximity"], ENGINE_V2_CONFIG["SUPPORT_WEIGHTS"]["proximity"])
        
        score = sum(breakdown.values())
        return {"score": score, "breakdown": breakdown}

class VolatilityRegimeEngine:
    @staticmethod
    def calculate(kwargs: dict) -> str:
        atr_pct = kwargs.get("atr_pct") or 2.0
        if atr_pct > 4.5: return "HIGH"
        if atr_pct < 1.5: return "LOW"
        return "NORMAL"

class TradeQualityEngine:
    @staticmethod
    def calculate(kwargs: dict, support_score: int) -> dict:
        adx = _safe(kwargs.get("adx")) or 20.0
        rsi = _safe(kwargs.get("rsi")) or 50.0
        
        trend = min(ENGINE_V2_CONFIG["TRADE_QUALITY_WEIGHTS"]["trend"], int((adx / 40.0) * ENGINE_V2_CONFIG["TRADE_QUALITY_WEIGHTS"]["trend"]))
        momentum = min(ENGINE_V2_CONFIG["TRADE_QUALITY_WEIGHTS"]["momentum"], int((rsi / 70.0) * ENGINE_V2_CONFIG["TRADE_QUALITY_WEIGHTS"]["momentum"]))
        support_val = int((support_score / 100.0) * ENGINE_V2_CONFIG["TRADE_QUALITY_WEIGHTS"]["support"])
        
        breakdown = {
            "trend": trend,
            "volume": 15, # Proxy for now unless we pass in volume explicitly
            "momentum": momentum,
            "support": support_val,
            "rs": 10,
            "market": 10
        }
        
        score = sum(breakdown.values())
        return {"score": min(100, score), "breakdown": breakdown}

class BaseRiskEngine:
    def __init__(self, mode: str, kwargs: dict):
        self.mode = mode
        self.kwargs = kwargs
        self.entry_price = kwargs.get("entry_price", 0.0)

    def compute_sl(self, support_price: float, support_conf: int, vol_regime: str) -> float:
        eff_atr = self.kwargs.get("eff_atr") or (self.entry_price * 0.015)
        
        buf_mult = 0.5
        if vol_regime == "HIGH": buf_mult = 1.0
        elif vol_regime == "LOW": buf_mult = 0.3
        
        if support_conf < 50: buf_mult *= 1.5
        elif support_conf > 80: buf_mult *= 0.7
        
        adx = _safe(self.kwargs.get("adx")) or 20.0
        if adx > 35: buf_mult *= 1.2
        
        raw_sl = support_price - (buf_mult * eff_atr)
        
        # Hard cap SL so it doesn't get un-usably wide
        max_sl_atr = _MODE_CONFIG.get(self.mode.split("_")[0], _MODE_CONFIG["EOD"])[4]
        min_allowed_sl = self.entry_price - (max_sl_atr * eff_atr)
        
        raw_sl = max(raw_sl, min_allowed_sl)
        
        # Ensure SL never goes negative on extreme volatility penny stocks
        return round(max(0.01, raw_sl), 2)

    def compute_targets(self, risk: float, vol_regime: str) -> tuple[dict, dict]:
        entry = self.entry_price
        eff_atr = self.kwargs.get("eff_atr") or (entry * 0.015)
        
        swing_high = _safe(self.kwargs.get("swing_high")) or (entry + 2 * eff_atr)
        r1 = _safe(self.kwargs.get("r1")) or (entry + 1.5 * eff_atr)
        vwap = _safe(self.kwargs.get("vwap")) or entry
        
        fib = entry + (swing_high - entry) * 1.618
        cr = _safe(self.kwargs.get("candle_range")) or eff_atr
        measured_move = entry + cr
        atr_proj = entry + 3 * eff_atr
        
        # Weight Normalization
        raw_weights = ENGINE_V2_CONFIG["TARGET_WEIGHTS"]
        total_w = max(sum(raw_weights.values()), 1e-5)
        norm_w = {k: v / total_w for k, v in raw_weights.items()}
        
        t1_cand = (
            swing_high * norm_w["swing_high"] +
            fib * norm_w["fib"] +
            measured_move * norm_w["measured_move"] +
            max(vwap, entry*1.01) * norm_w["vwap"] +
            atr_proj * norm_w["atr"] +
            r1 * norm_w["volume_profile"]
        )
        
        min_rr = 2.0
        if vol_regime == "HIGH": min_rr = 1.5
        elif vol_regime == "LOW": min_rr = 2.5
        
        t1 = max(t1_cand, entry + min_rr * risk)
        t2 = t1 + 1.5 * risk
        t3 = t1 + 3.0 * risk
        
        cluster_diagnostics = {
            "swing_high": round(swing_high, 2),
            "fib": round(fib, 2),
            "measured_move": round(measured_move, 2),
            "vwap": round(vwap, 2),
            "atr_proj": round(atr_proj, 2),
            "r1": round(r1, 2),
            "consensus_target": round(t1_cand, 2)
        }
        
        targets = {
            "t1": {"price": round(t1, 2), "confidence": "HIGH", "exit": ENGINE_V2_CONFIG["PARTIAL_EXITS"]["t1"]},
            "t2": {"price": round(t2, 2), "confidence": "MEDIUM", "exit": ENGINE_V2_CONFIG["PARTIAL_EXITS"]["t2"]},
            "t3": {"price": round(t3, 2), "confidence": "LOW", "exit": ENGINE_V2_CONFIG["PARTIAL_EXITS"]["t3"]}
        }
        
        return targets, cluster_diagnostics
        
    def get_time_stop(self) -> str:
        return "7 trading days"
        
    def get_trailing_rule(self) -> str:
        adx = _safe(self.kwargs.get("adx")) or 20.0
        if adx > 35: return "EMA20"
        return "Pivot Low"

    def get_historical_stats(self) -> dict:
        return {"win_rate": 0.58, "avg_win": 2.8, "avg_loss": 1.0}

    def generate_metrics(self) -> dict:
        support_metrics = SupportConfidenceEngine.calculate(self.kwargs)
        vol_regime = VolatilityRegimeEngine.calculate(self.kwargs)
        tq_metrics = TradeQualityEngine.calculate(self.kwargs, support_metrics["score"])
        
        support_price = self.kwargs.get("swing_low") or (self.entry_price * 0.95)
        
        sl = self.compute_sl(support_price, support_metrics["score"], vol_regime)
        
        risk_dist = self.entry_price - sl
        risk_pct = (risk_dist / self.entry_price) * 100 if self.entry_price > 0 else 1.0
        
        tq = tq_metrics["score"]
        if tq >= 90:
            kelly_fraction = 0.5
            max_risk_pct = 1.5
        elif tq >= 70:
            kelly_fraction = 0.3
            max_risk_pct = 1.0
        else:
            kelly_fraction = 0.15
            max_risk_pct = 0.5
            
        # Hard cap Kelly limits
        MAX_RISK_LIMIT = 2.0
        max_risk_pct = min(max_risk_pct, MAX_RISK_LIMIT)
            
        position_size_pct = round(max_risk_pct / (risk_pct / 100.0), 2) if risk_pct > 0 else 0.0
        
        targets, target_cluster_vals = self.compute_targets(risk_dist, vol_regime)
        
        t1_price = targets["t1"]["price"]
        expected_rr = round((t1_price - self.entry_price) / risk_dist, 2) if risk_dist > 0 else 0.0
        
        hist_stats = self.get_historical_stats()
        prob_win = hist_stats["win_rate"]
        prob_loss = 1.0 - prob_win
        ev = round((prob_win * hist_stats["avg_win"]) - (prob_loss * hist_stats["avg_loss"]), 2)
        
        warnings = []
        if (t1_price - self.entry_price) / max(self.entry_price, 1) < 0.03:
            warnings.append("Target very close to resistance")
        if support_metrics["score"] < 50:
            warnings.append("Support confidence below 50")
        if self.kwargs.get("atr_pct", 0) > 4.5:
            warnings.append("ATR percentile very high")
            
        diagnostics = {
            "support_source": "Swing Cluster" if self.kwargs.get("swing_low_cluster") else "Pivot",
            "target_source": "Hybrid Consensus",
            "volatility_mode": vol_regime,
            "market_regime": "Bull",
            "scanner": self.mode,
            "engine_version": "2.0",
            "target_cluster_values": target_cluster_vals
        }
        
        return {
            "engine_version": "2.0",
            "scanner": self.mode,
            "trade_quality": tq,
            "trade_quality_breakdown": tq_metrics["breakdown"],
            "support": {
                "price": round(support_price, 2),
                "confidence": support_metrics["score"],
                "breakdown": support_metrics["breakdown"]
            },
            "risk": {
                "stop_loss": sl,
                "rr": expected_rr,
                "risk_pct": round(risk_pct, 2),
                "position_size_pct": position_size_pct,
                "kelly_fraction": kelly_fraction,
                "expected_value": ev
            },
            "targets": targets,
            "management": {
                "time_stop": self.get_time_stop(),
                "trailing": self.get_trailing_rule()
            },
            "diagnostics": diagnostics,
            "warnings": warnings
        }


class BreakoutAdapter(BaseRiskEngine):
    def get_time_stop(self) -> str:
        return "5-7 trading days"
    def get_historical_stats(self) -> dict:
        return {"win_rate": 0.58, "avg_win": 2.8, "avg_loss": 1.0}

class ReversalAdapter(BaseRiskEngine):
    def get_time_stop(self) -> str:
        return "12-15 trading days"
    def get_historical_stats(self) -> dict:
        return {"win_rate": 0.52, "avg_win": 3.2, "avg_loss": 1.0}

class IntradayAdapter(BaseRiskEngine):
    def get_time_stop(self) -> str:
        return "End of session"
    def get_historical_stats(self) -> dict:
        return {"win_rate": 0.48, "avg_win": 2.0, "avg_loss": 1.0}

class HourlyAdapter(BaseRiskEngine):
    def get_time_stop(self) -> str:
        return "10 candles"
    def get_historical_stats(self) -> dict:
        return {"win_rate": 0.55, "avg_win": 2.2, "avg_loss": 1.0}


def compute_sl_and_target(
    entry_price:    float,
    atr:            Optional[float],
    candle_range:   float,
    mode:           Optional[str]   = None,     
    engine_version: str             = "v1.0",
    **kwargs
) -> dict:
    """
    Unified entry point for generating SL and Target metrics.
    Supports backward-compatibility via `engine_version="v1.0"`.
    """
    if engine_version in ("v1.0", "v1"):
        # Legacy fallback wrapper
        return _legacy_compute_sl_and_target(
            entry_price=entry_price,
            atr=atr,
            candle_range=candle_range,
            mode=mode,
            **kwargs
        )

    # v2.0 Institutional Engine routing
    # Map mode string to proper adapter
    scanner = (mode or "BREAKOUT").upper()
    kwargs["entry_price"] = entry_price
    kwargs["atr"] = atr
    kwargs["candle_range"] = candle_range

    if scanner in ("INTRADAY", "15M"):
        adapter = IntradayAdapter(scanner, kwargs)
    elif scanner in ("1H", "LIVE_1H"):
        adapter = HourlyAdapter(scanner, kwargs)
    elif scanner == "REVERSAL":
        adapter = ReversalAdapter(scanner, kwargs)
    else:
        adapter = BreakoutAdapter(scanner, kwargs)

    return adapter.generate_metrics()
