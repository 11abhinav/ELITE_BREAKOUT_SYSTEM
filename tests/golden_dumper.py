import os
import json
import pandas as pd
from typing import Dict, Any, List

SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "golden")

def _normalize_dict(d: dict) -> dict:
    """Recursively normalizes floats for stable JSON serialization."""
    if not isinstance(d, dict):
        if isinstance(d, float):
            return round(d, 4)
        return d
    
    out = {}
    for k, v in d.items():
        if isinstance(v, float):
            if "score" in k.lower() or "margin" in k.lower() or "ratio" in k.lower() or "pct" in k.lower():
                out[k] = round(v, 2)
            else:
                out[k] = round(v, 4)
        elif isinstance(v, dict):
            out[k] = _normalize_dict(v)
        elif isinstance(v, list):
            out[k] = [_normalize_dict(i) if isinstance(i, dict) else (round(i, 4) if isinstance(i, float) else i) for i in v]
        else:
            out[k] = v
    return out

def _write_json(filename: str, data: Any):
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    filepath = os.path.join(SNAPSHOT_DIR, filename)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)

class GoldenDumper:
    """
    Utility to capture pipeline stages and decision traces for the V4 architecture.
    """
    def __init__(self):
        self.traces: Dict[str, Dict[str, Any]] = {}
        
    def dump_infrastructure_cache(self, cache_state: dict):
        """00_cache_state.json - Infrastructure isolated."""
        _write_json("00_cache_state.json", cache_state)
        
    def dump_indicators(self, indicators_dict: Dict[str, pd.DataFrame]):
        """01_indicators_output.json - Tail end of the latest bar only to save space."""
        # Convert the last row of each dataframe to a dict
        out = {}
        for sym, df in indicators_dict.items():
            if df is not None and not df.empty:
                last_row = df.iloc[-1].to_dict()
                # Remove Timestamp for stable diffs if it exists
                if "Timestamp" in last_row:
                    del last_row["Timestamp"]
                out[sym] = _normalize_dict(last_row)
        _write_json("01_indicators_output.json", out)

    def dump_breakout_candidates(self, candidates: List[dict]):
        """02_breakout_candidates.json"""
        out = [_normalize_dict(c) for c in candidates]
        _write_json("02_breakout_candidates.json", out)
        
    def dump_reversal_candidates(self, candidates: List[dict]):
        """03_reversal_candidates.json"""
        out = [_normalize_dict(c) for c in candidates]
        _write_json("03_reversal_candidates.json", out)
        
    def dump_final_alerts(self, alerts: List[dict]):
        """04_final_alerts.json"""
        out = [_normalize_dict(a) for a in alerts]
        _write_json("04_final_alerts.json", out)

    def record_trace(self, symbol: str, decision: str, reasons: List[str]):
        """Layer 7: Decision Trace capturing."""
        self.traces[symbol] = {
            "symbol": symbol,
            "decision": decision,
            "reasons": sorted(reasons)
        }
        
    def dump_traces(self):
        """05_decision_traces.json"""
        _write_json("05_decision_traces.json", self.traces)
