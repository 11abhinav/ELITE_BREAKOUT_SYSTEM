import re
import sys

with open('app/sl_target_helper.py', 'r') as f:
    content = f.read()

# 1. Replace _MODE_CONFIG
old_mode = """_MODE_CONFIG = {
    #           atr_base  sl_atr_buf  sl_pct_buf  max_sl_atr
    "EOD":      (2.00,    0.75,       0.0075,     3.0),

    "REVERSAL": (2.00,    1.00,       0.0100,     3.5),
}"""

new_mode = """_MODE_CONFIG = {
    #           atr_base  sl_atr_buf  sl_pct_buf  max_sl_atr
    "EOD":      (2.00,    0.80,       0.0075,     3.0),   # Balanced
    "MULTI_TF": (1.50,    0.50,       0.0050,     3.0),   # Aggressive
    "REVERSAL": (2.00,    1.00,       0.0100,     3.5),   # Wide
}"""

if old_mode in content:
    content = content.replace(old_mode, new_mode)
else:
    print("WARNING: Could not find old _MODE_CONFIG")

# 2. Insert v7 Classes
v7_code = """
# ── Target Engine v7 Classes ──────────────────────────────────────────────────
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from abc import ABC, abstractmethod
from config import (
    TARGET_SOURCE_WEIGHTS, SOURCE_PRIORITY, TARGET_CONFLICT_POLICY,
    EXIT_PROFILES, SCANNER_EXIT_PROFILE, FIB_EXTENSIONS, FIB_RETRACEMENTS,
    ABCD_BC_RETRACE_MIN, ABCD_BC_RETRACE_MAX, FIB_200_GATE,
    ROUND_NUMBER_BOOST, ROUND_NUMBER_PCT, TARGET_CLUSTER_WINDOW_ATR_FRAC,
    TARGET_CLUSTER_WINDOW_PCT, FIB_200_WEIGHTS
)

class TargetSource(Enum):
    RESISTANCE    = "RESISTANCE"
    EQUAL_HIGH    = "EQUAL_HIGH"
    PREV_DAY_HIGH = "PREV_DAY_HIGH"
    HIGH_20D      = "HIGH_20D"
    HIGH_52W      = "HIGH_52W"
    ABCD          = "ABCD"
    FIB_127       = "FIB_127"
    FIB_162       = "FIB_162"
    FIB_200       = "FIB_200"
    BB_MID        = "BB_MID"
    SMA50         = "SMA50"
    SMA200        = "SMA200"
    RETRACE_382   = "RETRACE_382"
    RETRACE_50    = "RETRACE_50"
    RETRACE_618   = "RETRACE_618"
    SWING_HIGH_RAW = "SWING_HIGH_RAW"
    ATR_PROJ      = "ATR_PROJ"
    R1            = "R1"
    R2            = "R2"
    ROUND_NUM     = "ROUND_NUM"

@dataclass
class TargetCandidate:
    price:         float
    source:        TargetSource
    timeframe:     str
    scanner:       str
    strength:      str
    anchor_points: dict
    generated_from: str = ""
    cluster_id:    Optional[int] = None
    score:         int = 0
    selection_state: str = "REJECTED"
    is_round_number: bool = False

@dataclass
class ClusteredTarget:
    cluster_id: int
    consensus_price: float
    score: int
    candidates: List[TargetCandidate]
    is_round_number: bool = False
    
class TargetScorer:
    @staticmethod
    def score(candidate: TargetCandidate, macro_regime: str) -> int:
        source_name = candidate.source.name
        if source_name == "FIB_200":
            return FIB_200_WEIGHTS.get(macro_regime, 5)
        return TARGET_SOURCE_WEIGHTS.get(source_name, 0)

class RoundNumberEngine:
    @staticmethod
    def _get_tick(price: float) -> float:
        if price < 50: return 2.0
        if price < 100: return 5.0
        if price < 200: return 10.0
        if price < 500: return 25.0
        if price < 1000: return 50.0
        if price < 2000: return 100.0
        if price < 5000: return 250.0
        return 1000.0

    @staticmethod
    def detect_and_boost(clusters: List[ClusteredTarget]) -> None:
        for c in clusters:
            tick = RoundNumberEngine._get_tick(c.consensus_price)
            nearest = round(c.consensus_price / tick) * tick
            pct_diff = abs(c.consensus_price - nearest) / c.consensus_price
            if pct_diff <= ROUND_NUMBER_PCT:
                c.is_round_number = True
                c.score += ROUND_NUMBER_BOOST
                c.candidates.append(TargetCandidate(
                    price=nearest, source=TargetSource.ROUND_NUM,
                    timeframe="any", scanner="any", strength="NORMAL",
                    anchor_points={}, generated_from="RoundNumberEngine",
                    cluster_id=c.cluster_id, score=0, is_round_number=True
                ))

class ClusterEngine:
    @staticmethod
    def _consensus_price(candidates: List[TargetCandidate]) -> float:
        ranked = sorted(candidates, key=lambda c: SOURCE_PRIORITY.get(c.source.name, 99))
        return ranked[0].price

    @staticmethod
    def cluster(candidates: List[TargetCandidate], entry: float, eff_atr: float) -> List[ClusteredTarget]:
        if not candidates: return []
        window = max(TARGET_CLUSTER_WINDOW_ATR_FRAC * eff_atr, TARGET_CLUSTER_WINDOW_PCT * entry)
        sorted_cands = sorted(candidates, key=lambda c: c.price)
        
        clusters = []
        current_cluster_cands = [sorted_cands[0]]
        cluster_min = sorted_cands[0].price
        
        for cand in sorted_cands[1:]:
            if cand.price - cluster_min <= window:
                current_cluster_cands.append(cand)
            else:
                clusters.append(current_cluster_cands)
                current_cluster_cands = [cand]
                cluster_min = cand.price
        clusters.append(current_cluster_cands)
        
        result = []
        for i, c_cands in enumerate(clusters):
            for c in c_cands:
                c.cluster_id = i
            c_price = ClusterEngine._consensus_price(c_cands)
            c_score = sum(c.score for c in c_cands)
            result.append(ClusteredTarget(
                cluster_id=i, consensus_price=c_price, score=c_score, candidates=c_cands
            ))
        return result

class LiquidityEngine:
    @staticmethod
    def detect_equal_highs(ticker: pd.DataFrame, entry: float) -> Optional[float]:
        if ticker is None or ticker.empty: return None
        recent = ticker.tail(60)
        highs = recent["High"].values
        for i in range(len(highs)):
            for j in range(i+1, len(highs)):
                if highs[i] > entry and highs[j] > entry:
                    if abs(highs[i] - highs[j]) / highs[i] < 0.005:
                        return float((highs[i] + highs[j]) / 2)
        return None

class ABCDDetector:
    BC_RETRACE_MIN = 0.382
    BC_RETRACE_MAX = 0.786

    @staticmethod
    def detect(ticker: pd.DataFrame, entry: float) -> Optional[float]:
        if ticker is None or ticker.empty or "SWING_LOW" not in ticker.columns or "SWING_HIGH" not in ticker.columns:
            return None
        pivot_lows  = ticker["SWING_LOW"].dropna()
        pivot_highs = ticker["SWING_HIGH"].dropna()

        if len(pivot_lows) < 2 or len(pivot_highs) < 1:
            return None

        for c_idx, C in reversed(list(pivot_lows.items())):
            b_candidates = pivot_highs[pivot_highs.index < c_idx]
            if b_candidates.empty: continue
            b_idx = b_candidates.index[-1]
            B = b_candidates.iloc[-1]
            if B <= C: continue

            a_candidates = pivot_lows[pivot_lows.index < b_idx]
            if a_candidates.empty: continue
            A = a_candidates.iloc[-1]
            if A >= B: continue

            AB = B - A
            BC = B - C
            BC_ratio = BC / AB if AB > 0 else 0

            if not (ABCDDetector.BC_RETRACE_MIN <= BC_ratio <= ABCDDetector.BC_RETRACE_MAX):
                continue

            D_projection = C + AB
            if D_projection > entry:
                return round(float(D_projection), 2)
        return None

class CandidateGenerator:
    def generate_breakout_candidates(
        self, entry: float, eff_atr: float, atr_pct: float, adx: float, volume_ratio: float,
        vwap: float, macro_regime: str, scanner: str,
        swing_low: float, swing_high: float, swing_low_raw: float, swing_high_raw: float,
        r1: float, r2: float, bb_upper: float, prior_20d_high: float, high_52w: float, prev_day_high: float,
        ticker: pd.DataFrame
    ) -> List[TargetCandidate]:
        candidates = []
        
        # Resistance
        from sl_target_helper import _pick_resistance, _safe
        res, label = _pick_resistance(entry, swing_high, r1, bb_upper, swing_high_raw, r2)
        if res:
            candidates.append(TargetCandidate(price=res, source=TargetSource.RESISTANCE, timeframe="any", scanner=scanner, strength="NORMAL", anchor_points={"label": label}))
            
        if _safe(prev_day_high) and prev_day_high > entry:
            candidates.append(TargetCandidate(price=prev_day_high, source=TargetSource.PREV_DAY_HIGH, timeframe="1d", scanner=scanner, strength="NORMAL", anchor_points={}))

        if _safe(prior_20d_high) and prior_20d_high > entry:
            candidates.append(TargetCandidate(price=prior_20d_high, source=TargetSource.HIGH_20D, timeframe="1d", scanner=scanner, strength="NORMAL", anchor_points={}))

        if _safe(high_52w) and high_52w > entry:
            candidates.append(TargetCandidate(price=high_52w, source=TargetSource.HIGH_52W, timeframe="1d", scanner=scanner, strength="STRONG", anchor_points={}))

        eq_high = LiquidityEngine.detect_equal_highs(ticker, entry)
        if eq_high:
            candidates.append(TargetCandidate(price=eq_high, source=TargetSource.EQUAL_HIGH, timeframe="any", scanner=scanner, strength="STRONG", anchor_points={}))

        leg = None
        if _safe(swing_high_raw) and _safe(swing_low_raw):
            leg = swing_high_raw - swing_low_raw

        if leg and leg > 0:
            candidates.append(TargetCandidate(price=entry + leg * 1.272, source=TargetSource.FIB_127, timeframe="any", scanner=scanner, strength="NORMAL", anchor_points={"leg": leg}))
            candidates.append(TargetCandidate(price=entry + leg * 1.618, source=TargetSource.FIB_162, timeframe="any", scanner=scanner, strength="NORMAL", anchor_points={"leg": leg}))

            fib200_allowed = (
                _safe(adx) and adx > FIB_200_GATE["min_adx"]
                and _safe(volume_ratio) and volume_ratio > FIB_200_GATE["min_vol_ratio"]
                and _safe(vwap) and entry > vwap
                and macro_regime in ("TRENDING", "BULL")
            )
            if fib200_allowed:
                candidates.append(TargetCandidate(price=entry + leg * 2.0, source=TargetSource.FIB_200, timeframe="any", scanner=scanner, strength="NORMAL", anchor_points={"leg": leg}))

        abcd_price = ABCDDetector.detect(ticker, entry)
        if abcd_price:
            candidates.append(TargetCandidate(price=abcd_price, source=TargetSource.ABCD, timeframe="any", scanner=scanner, strength="NORMAL", anchor_points={}))

        candidates.append(TargetCandidate(price=entry + 3 * eff_atr, source=TargetSource.ATR_PROJ, timeframe="any", scanner=scanner, strength="NORMAL", anchor_points={}))

        for c in candidates:
            c.score = TargetScorer.score(c, macro_regime)

        return candidates

class TargetStrategy(ABC):
    def pre_filter(self, candidates: List[TargetCandidate], context: dict) -> List[TargetCandidate]:
        return candidates

    @abstractmethod
    def select_targets(self, clusters: List[ClusteredTarget], entry: float, risk: float, context: dict) -> dict:
        pass

    def post_filter(self, result: dict, context: dict) -> dict:
        return result

class TrendExtensionStrategy(TargetStrategy):
    def select_targets(self, clusters: List[ClusteredTarget], entry: float, risk: float, context: dict) -> dict:
        if not clusters: return {}
        # MULTI_TF uses CONFIDENCE policy
        ranked = sorted(clusters, key=lambda c: c.score, reverse=True)
        t1 = ranked[0]
        t2 = ranked[1] if len(ranked) > 1 else t1
        t3 = ranked[2] if len(ranked) > 2 else t2
        return {"t1": t1.consensus_price, "t2": t2.consensus_price, "t3": t3.consensus_price, "t1_cluster": t1, "t2_cluster": t2, "t3_cluster": t3}

class ClusterConsensusStrategy(TargetStrategy):
    def select_targets(self, clusters: List[ClusteredTarget], entry: float, risk: float, context: dict) -> dict:
        if not clusters: return {}
        # EOD uses REGIME policy - for simplicity we rank by score + distance
        ranked = sorted(clusters, key=lambda c: c.score, reverse=True)
        t1 = ranked[0]
        t2 = ranked[1] if len(ranked) > 1 else t1
        t3 = ranked[2] if len(ranked) > 2 else t2
        return {"t1": t1.consensus_price, "t2": t2.consensus_price, "t3": t3.consensus_price, "t1_cluster": t1, "t2_cluster": t2, "t3_cluster": t3}

class MeanReversionStrategy(TargetStrategy):
    def select_targets(self, clusters: List[ClusteredTarget], entry: float, risk: float, context: dict) -> dict:
        # Reversal simply walks the stack
        return {} # Will be custom implemented in _compute_reversal

class ConflictResolver:
    @staticmethod
    def resolve(clusters: List[ClusteredTarget], scanner: str, entry: float, macro_regime: str) -> List[ClusteredTarget]:
        policy = TARGET_CONFLICT_POLICY.get(scanner, "CONFIDENCE")
        if policy == "NEAREST":
            return sorted(clusters, key=lambda c: c.consensus_price)
        elif policy == "CONFIDENCE":
            return sorted(clusters, key=lambda c: c.score, reverse=True)
        elif policy == "REGIME":
            if macro_regime in ("BULL", "TRENDING"):
                return sorted(clusters, key=lambda c: c.consensus_price, reverse=True) # Prefer higher
            else:
                return sorted(clusters, key=lambda c: c.score, reverse=True)
        return clusters

class ExitPolicy:
    @staticmethod
    def get_profile(scanner: str) -> dict:
        profile_name = SCANNER_EXIT_PROFILE.get(scanner, "BALANCED")
        return EXIT_PROFILES.get(profile_name, EXIT_PROFILES["BALANCED"])

"""

if "TargetSource" not in content:
    idx = content.find("_DEFAULT_CONFIG = (1.50, 0.50, 0.0050, 3.0)")
    if idx != -1:
        idx += len("_DEFAULT_CONFIG = (1.50, 0.50, 0.0050, 3.0)")
        content = content[:idx] + "\n\n" + v7_code + content[idx:]
    else:
        print("Could not find insertion point for v7 classes.")

with open('app/sl_target_helper.py', 'w') as f:
    f.write(content)
