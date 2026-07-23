import sys

def build_replacement():
    return """
def _compute_multi_tf(entry: float, eff_atr: float, atr_pct: float, adx: float, rsi: float, macd_hist: float, swing_low: float, swing_high: float, s1: float, s2: float, r1: float, r2: float, swing_low_raw: float, swing_high_raw: float, ticker=None, **kwargs) -> dict:
    supports = [
        (swing_low, "5m Swing Low", 20),
        (kwargs.get("swing_low_15m"), "15m Swing Low", 25),
        (kwargs.get("swing_low_30m"), "30m Swing Low", 30),
        (kwargs.get("swing_low_1h"), "1H Swing Low", 35),
        (s1, "S1", 20),
        (s2, "S2", 15),
        (swing_low_raw, "Rolling Swing Low", 20),
        (kwargs.get("vwap"), "VWAP", 15),
        (kwargs.get("ema20"), "EMA20", 15),
        (kwargs.get("sma50"), "SMA50", 15),
        (kwargs.get("sma200"), "SMA200", 30)
    ]
    sl_data = _compute_structural_stop(entry, eff_atr, atr_pct, supports, {"mode": "MULTI_TF"})
    if not sl_data.get("is_valid", True):
        return {
            "engine_version": "SL_ENGINE_V7", "is_rejected": True, "rejection_reason": "NO_VALID_STRUCTURAL_STOP",
            "gate": "MIN_STOP_PCT", "actual": sl_data.get("details", {}).get("best_stop_pct", 0.0),
            "required": sl_data.get("details", {}).get("required_stop_pct", 0.0), "context": sl_data.get("details", {}),
            "stop_loss": sl_data["raw_sl"], "target_1": entry, "natural_rr": 0.0, "sl_result": sl_data
        }

    macro_regime = kwargs.get("macro_regime", "NEUTRAL")
    gen = CandidateGenerator()
    candidates = gen.generate_breakout_candidates(
        entry=entry, eff_atr=eff_atr, atr_pct=atr_pct, adx=adx, volume_ratio=kwargs.get("volume_ratio", 1.0),
        vwap=kwargs.get("vwap"), macro_regime=macro_regime, scanner="MULTI_TF",
        swing_low=swing_low, swing_high=swing_high, swing_low_raw=swing_low_raw, swing_high_raw=swing_high_raw,
        r1=r1, r2=r2, bb_upper=kwargs.get("bb_upper"), prior_20d_high=kwargs.get("prior_20d_high"),
        high_52w=kwargs.get("high_52w"), prev_day_high=kwargs.get("prev_day_high"), ticker=ticker
    )
    
    strategy = TrendExtensionStrategy()
    candidates = strategy.pre_filter(candidates, {"vwap": kwargs.get("vwap")})
    
    clusters = ClusterEngine.cluster(candidates, entry, eff_atr)
    RoundNumberEngine.detect_and_boost(clusters)
    clusters = ConflictResolver.resolve(clusters, "MULTI_TF", entry, macro_regime)
    
    targets = strategy.select_targets(clusters, entry, entry - sl_data["raw_sl"], {})
    
    pool = []
    for c in candidates:
        c_dict = vars(c).copy()
        c_dict["source"] = c.source.name
        if targets and targets.get("t1_cluster") and c.cluster_id == targets["t1_cluster"].cluster_id:
            c_dict["selection_state"] = "WINNING"
        elif targets and ( (targets.get("t2_cluster") and c.cluster_id == targets["t2_cluster"].cluster_id) or (targets.get("t3_cluster") and c.cluster_id == targets["t3_cluster"].cluster_id) ):
            c_dict["selection_state"] = "SELECTED"
        else:
            c_dict["selection_state"] = "REJECTED"
        pool.append(c_dict)

    t1 = targets.get("t1", entry)
    t1_src = targets.get("t1_cluster").candidates[0].source.name if targets.get("t1_cluster") else "UNKNOWN"
    return {
        "engine_version": "SL_ENGINE_V7", "stop_loss": sl_data["raw_sl"],
        "target_1": t1, "target_2": targets.get("t2"), "target_3": targets.get("t3"),
        "rr_ratio": round(abs(t1 - entry) / abs(entry - sl_data["raw_sl"]), 2) if sl_data["raw_sl"] != entry else 0.0,
        "sl_method": sl_data["sl_method"], "t_method": f"TrendExtension [T1:{t1_src}]",
        "sl_result": {"target_candidate_pool": pool, "t1_source": t1_src}
    }

def _compute_eod(entry: float, eff_atr: float, atr_pct: float, adx: float, rsi: float, macd_hist: float, swing_low: float, swing_high: float, s1: float, s2: float, r1: float, r2: float, swing_low_raw: float, swing_high_raw: float, ticker=None, **kwargs) -> dict:
    supports = [
        (swing_low, "True Swing Low", 40), (s1, "S1 Pivot", 20), (s2, "S2 Pivot", 15),
        (swing_low_raw, "Rolling Low", 20), (kwargs.get("sma50"), "SMA50", 15), (kwargs.get("sma200"), "SMA200", 30)
    ]
    sl_data = _compute_structural_stop(entry, eff_atr, atr_pct, supports, {"mode": "EOD"})
    if not sl_data.get("is_valid", True):
        return {
            "engine_version": "SL_ENGINE_V7", "is_rejected": True, "rejection_reason": "NO_VALID_STRUCTURAL_STOP",
            "stop_loss": sl_data["raw_sl"], "target_1": entry, "natural_rr": 0.0, "sl_result": sl_data
        }

    macro_regime = kwargs.get("macro_regime", "NEUTRAL")
    gen = CandidateGenerator()
    candidates = gen.generate_breakout_candidates(
        entry=entry, eff_atr=eff_atr, atr_pct=atr_pct, adx=adx, volume_ratio=kwargs.get("volume_ratio", 1.0),
        vwap=kwargs.get("vwap"), macro_regime=macro_regime, scanner="EOD",
        swing_low=swing_low, swing_high=swing_high, swing_low_raw=swing_low_raw, swing_high_raw=swing_high_raw,
        r1=r1, r2=r2, bb_upper=kwargs.get("bb_upper"), prior_20d_high=kwargs.get("prior_20d_high"),
        high_52w=kwargs.get("high_52w"), prev_day_high=kwargs.get("prev_day_high"), ticker=ticker
    )
    
    strategy = ClusterConsensusStrategy()
    clusters = ClusterEngine.cluster(candidates, entry, eff_atr)
    RoundNumberEngine.detect_and_boost(clusters)
    clusters = ConflictResolver.resolve(clusters, "EOD", entry, macro_regime)
    targets = strategy.select_targets(clusters, entry, entry - sl_data["raw_sl"], {})
    
    pool = []
    for c in candidates:
        c_dict = vars(c).copy()
        c_dict["source"] = c.source.name
        if targets and targets.get("t1_cluster") and c.cluster_id == targets["t1_cluster"].cluster_id:
            c_dict["selection_state"] = "WINNING"
        elif targets and ( (targets.get("t2_cluster") and c.cluster_id == targets["t2_cluster"].cluster_id) or (targets.get("t3_cluster") and c.cluster_id == targets["t3_cluster"].cluster_id) ):
            c_dict["selection_state"] = "SELECTED"
        else:
            c_dict["selection_state"] = "REJECTED"
        pool.append(c_dict)

    t1 = targets.get("t1", entry)
    t1_src = targets.get("t1_cluster").candidates[0].source.name if targets.get("t1_cluster") else "UNKNOWN"
    return {
        "engine_version": "SL_ENGINE_V7", "stop_loss": sl_data["raw_sl"],
        "target_1": t1, "target_2": targets.get("t2"), "target_3": targets.get("t3"),
        "rr_ratio": round(abs(t1 - entry) / abs(entry - sl_data["raw_sl"]), 2) if sl_data["raw_sl"] != entry else 0.0,
        "sl_method": sl_data["sl_method"], "t_method": f"ClusterConsensus [T1:{t1_src}]",
        "sl_result": {"target_candidate_pool": pool, "t1_source": t1_src}
    }

def _compute_reversal(entry: float, eff_atr: float, atr_pct: float, adx: float, rsi: float, macd_hist: float, swing_low: float, swing_high: float, s1: float, s2: float, r1: float, r2: float, swing_low_raw: float, swing_high_raw: float, ticker=None, **kwargs) -> dict:
    supports = [
        (swing_low, "True Swing Low", 40), (s1, "S1 Pivot", 20), (s2, "S2 Pivot", 15),
        (swing_low_raw, "Rolling Low", 20)
    ]
    sl_data = _compute_structural_stop(entry, eff_atr, atr_pct, supports, {"mode": "REVERSAL"})
    if not sl_data.get("is_valid", True):
        return {
            "engine_version": "SL_ENGINE_V7", "is_rejected": True, "rejection_reason": "NO_VALID_STRUCTURAL_STOP",
            "stop_loss": sl_data["raw_sl"], "target_1": entry, "natural_rr": 0.0, "sl_result": sl_data
        }

    # Mean reversion stack
    cands = []
    prior_high = swing_high_raw if _safe(swing_high_raw) else entry + 5 * eff_atr
    decline = prior_high - entry if prior_high > entry else 0

    if _safe(kwargs.get("bb_mid")): cands.append(TargetCandidate(kwargs.get("bb_mid"), TargetSource.BB_MID, "any", "REVERSAL", "NORMAL", {}))
    if _safe(kwargs.get("sma50")): cands.append(TargetCandidate(kwargs.get("sma50"), TargetSource.SMA50, "any", "REVERSAL", "NORMAL", {}))
    if decline > 0:
        cands.append(TargetCandidate(entry + decline*0.382, TargetSource.RETRACE_382, "any", "REVERSAL", "NORMAL", {}))
        cands.append(TargetCandidate(entry + decline*0.500, TargetSource.RETRACE_50, "any", "REVERSAL", "NORMAL", {}))
        cands.append(TargetCandidate(entry + decline*0.618, TargetSource.RETRACE_618, "any", "REVERSAL", "NORMAL", {}))
    if _safe(kwargs.get("sma200")): cands.append(TargetCandidate(kwargs.get("sma200"), TargetSource.SMA200, "any", "REVERSAL", "NORMAL", {}))
    if _safe(swing_high_raw): cands.append(TargetCandidate(swing_high_raw, TargetSource.SWING_HIGH_RAW, "any", "REVERSAL", "NORMAL", {}))
    
    # Filter only above entry
    cands = [c for c in cands if c.price > entry]
    
    clusters = ClusterEngine.cluster(cands, entry, eff_atr)
    RoundNumberEngine.detect_and_boost(clusters)
    clusters = ConflictResolver.resolve(clusters, "REVERSAL", entry, kwargs.get("macro_regime", "NEUTRAL"))
    
    t1 = clusters[0].consensus_price if clusters else entry + 2*eff_atr
    t2 = clusters[1].consensus_price if len(clusters) > 1 else None
    t3 = clusters[2].consensus_price if len(clusters) > 2 else None
    
    return {
        "engine_version": "SL_ENGINE_V7", "stop_loss": sl_data["raw_sl"],
        "target_1": t1, "target_2": t2, "target_3": t3,
        "rr_ratio": round(abs(t1 - entry) / abs(entry - sl_data["raw_sl"]), 2) if sl_data["raw_sl"] != entry else 0.0,
        "sl_method": sl_data["sl_method"], "t_method": f"MeanReversion [T1]",
        "sl_result": {"target_candidate_pool": [vars(c) for c in cands]}
    }
"""

with open('app/sl_target_helper.py', 'r') as f:
    content = f.read()

import re

# We will just replace everything from 'def _compute_multi_tf(' to 'def _legacy_compute_sl_and_target('
start_idx = content.find("def _compute_multi_tf(")
end_idx = content.find("def _legacy_compute_sl_and_target(")

if start_idx != -1 and end_idx != -1:
    new_content = content[:start_idx] + build_replacement() + "\n\n" + content[end_idx:]
    with open('app/sl_target_helper.py', 'w') as f:
        f.write(new_content)
    print("SUCCESS")
else:
    print("FAILED TO FIND BOUNDS")
