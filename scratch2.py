import re

with open("app/sl_target_helper.py", "r") as f:
    code = f.read()

# 1. Update _compute_reversal
reversal_pattern = r"""    # Filter only above entry and enforce MIN_NATURAL_RR.*?cands = valid_cands.*?clusters = ConflictResolver.resolve\(clusters, "REVERSAL", entry, kwargs\.get\("macro_regime", "NEUTRAL"\)\)"""
reversal_replace = """    # Filter only above entry
    valid_cands = [c for c in cands if c.price > entry]
    
    risk = abs(entry - sl_data["raw_sl"])
    clusters = ClusterEngine.cluster(valid_cands, entry, eff_atr)
    RoundNumberEngine.detect_and_boost(clusters)
    clusters, rejection_reason = ConflictResolver.resolve(clusters, "REVERSAL", entry, kwargs.get("macro_regime", "NEUTRAL"), risk, eff_atr)
    
    if not clusters:
        return {
            "engine_version": "SL_ENGINE_V7.3", "is_rejected": True, 
            "rejection_reason": rejection_reason,
            "stop_loss": sl_data["raw_sl"], "target_1": entry, "natural_rr": 0.0, "sl_result": sl_data
        }"""
code = re.sub(reversal_pattern, reversal_replace, code, flags=re.DOTALL)

# 2. Update _compute_eod
eod_pattern = r"""    strategy = ClusterConsensusStrategy\(\).*?clusters = ConflictResolver.resolve\(clusters, "EOD", entry, macro_regime\).*?targets = strategy.select_targets\(clusters, entry, entry - sl_data\["raw_sl"\], \{\}\)"""
eod_replace = """    risk = abs(entry - sl_data["raw_sl"])
    clusters = ClusterEngine.cluster(candidates, entry, eff_atr)
    RoundNumberEngine.detect_and_boost(clusters)
    clusters, rejection_reason = ConflictResolver.resolve(clusters, "EOD", entry, macro_regime, risk, eff_atr)
    
    if not clusters:
        return {
            "engine_version": "SL_ENGINE_V7.3", "is_rejected": True, 
            "rejection_reason": rejection_reason,
            "stop_loss": sl_data["raw_sl"], "target_1": entry, "natural_rr": 0.0, "sl_result": sl_data
        }
        
    strategy = ClusterConsensusStrategy()
    targets = strategy.select_targets(clusters, entry, risk, {})"""
code = re.sub(eod_pattern, eod_replace, code, flags=re.DOTALL)

# Remove the redundant MIN_NATURAL_RR block in _compute_eod
eod_redundant = r"""    from config import MIN_NATURAL_RR\n    min_rr = MIN_NATURAL_RR.get\("EOD", 2\.5\)\n    if natural_rr_val < min_rr:\n        return \{\n            "engine_version": "SL_ENGINE_V7\.1", "is_rejected": True, \n            "rejection_reason": f"NO_VALID_STRUCTURAL_TARGET \(Min RR: \{min_rr\}x, Actual: \{natural_rr_val\}x\)",\n            "stop_loss": sl_data\["raw_sl"\], "target_1": entry, "natural_rr": natural_rr_val, "sl_result": sl_data\n        \}"""
code = re.sub(eod_redundant, "", code, flags=re.DOTALL)

# 3. Update _compute_multi_tf
multi_pattern = r"""    strategy = TrendExtensionStrategy\(\).*?clusters = ConflictResolver.resolve\(clusters, "MULTI_TF", entry, macro_regime\).*?targets = strategy.select_targets\(clusters, entry, entry - sl_data\["raw_sl"\], \{\}\)"""
multi_replace = """    risk = abs(entry - sl_data["raw_sl"])
    clusters = ClusterEngine.cluster(candidates, entry, eff_atr)
    RoundNumberEngine.detect_and_boost(clusters)
    clusters, rejection_reason = ConflictResolver.resolve(clusters, "MULTI_TF", entry, macro_regime, risk, eff_atr)
    
    if not clusters:
        return {
            "engine_version": "SL_ENGINE_V7.3", "is_rejected": True, 
            "rejection_reason": rejection_reason,
            "stop_loss": sl_data["raw_sl"], "target_1": entry, "natural_rr": 0.0, "sl_result": sl_data
        }
        
    strategy = TrendExtensionStrategy()
    targets = strategy.select_targets(clusters, entry, risk, {})"""
code = re.sub(multi_pattern, multi_replace, code, flags=re.DOTALL)

# Remove redundant MIN_NATURAL_RR in _compute_multi_tf
multi_redundant = r"""    from config import MIN_NATURAL_RR\n    min_rr = MIN_NATURAL_RR.get\("MULTI_TF", 1\.5\)\n    if natural_rr_val < min_rr:\n        return \{\n            "engine_version": "SL_ENGINE_V7\.1", "is_rejected": True, \n            "rejection_reason": f"NO_VALID_STRUCTURAL_TARGET \(Min RR: \{min_rr\}x, Actual: \{natural_rr_val\}x\)",\n            "stop_loss": sl_data\["raw_sl"\], "target_1": entry, "natural_rr": natural_rr_val, "sl_result": sl_data\n        \}"""
code = re.sub(multi_redundant, "", code, flags=re.DOTALL)

with open("app/sl_target_helper.py", "w") as f:
    f.write(code)
print("Updated successfully")
