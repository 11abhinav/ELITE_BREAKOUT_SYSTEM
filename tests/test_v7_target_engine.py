import pytest
import pandas as pd
from app.sl_target_helper import (
    CandidateGenerator, TargetSource, TargetCandidate, ClusterEngine,
    ABCDDetector, ConflictResolver, ClusteredTarget, RoundNumberEngine, LiquidityEngine, TargetScorer
)

def test_target_scorer():
    c = TargetCandidate(price=100.0, source=TargetSource.FIB_200, timeframe="any", scanner="any", strength="NORMAL", anchor_points={})
    score = TargetScorer.score(c, "BULL")
    assert score == 7
    score = TargetScorer.score(c, "BEAR")
    assert score == 2
    c2 = TargetCandidate(price=100.0, source=TargetSource.RESISTANCE, timeframe="any", scanner="any", strength="NORMAL", anchor_points={})
    score = TargetScorer.score(c2, "BULL")
    assert score == 10

def test_cluster_engine():
    candidates = [
        TargetCandidate(price=100.5, source=TargetSource.RESISTANCE, timeframe="1d", scanner="EOD", strength="NORMAL", anchor_points={}, score=10),
        TargetCandidate(price=100.8, source=TargetSource.PREV_DAY_HIGH, timeframe="1d", scanner="EOD", strength="NORMAL", anchor_points={}, score=9),
        TargetCandidate(price=105.0, source=TargetSource.FIB_127, timeframe="any", scanner="EOD", strength="NORMAL", anchor_points={}, score=7)
    ]
    # Cluster window = max(0.5 * 2.0 = 1.0, 0.0075 * 100 = 0.75) = 1.0
    clusters = ClusterEngine.cluster(candidates, entry=100.0, eff_atr=2.0)
    assert len(clusters) == 2
    assert clusters[0].consensus_price == 100.5 # Resistance prioritized
    assert clusters[0].score == 19
    assert clusters[1].consensus_price == 105.0

def test_abcd_detector():
    df = pd.DataFrame({
        "SWING_LOW": [None, 90.0, None, 95.0],
        "SWING_HIGH": [100.0, None, 105.0, None]
    }, index=[0, 1, 2, 3])
    # A = 90 (idx 1)
    # B = 105 (idx 2)
    # C = 95 (idx 3)
    # AB = 15. BC = 10. BC_ratio = 10/15 = 0.66 (Valid)
    # D = C + AB = 95 + 15 = 110
    d = ABCDDetector.detect(df, entry=106.0)
    assert d == 110.0

def test_round_number_engine():
    clusters = [
        ClusteredTarget(cluster_id=0, consensus_price=99.8, score=10, candidates=[])
    ]
    RoundNumberEngine.detect_and_boost(clusters)
    assert clusters[0].is_round_number == True
    assert clusters[0].score == 18 # 10 + 8
    assert len(clusters[0].candidates) == 1
    assert clusters[0].candidates[0].price == 100.0

def test_conflict_resolver_eod_bull():
    clusters = [
        ClusteredTarget(cluster_id=0, consensus_price=105.0, score=10, candidates=[]),
        ClusteredTarget(cluster_id=1, consensus_price=110.0, score=8, candidates=[])
    ]
    # EOD BULL should pick highest price
    resolved = ConflictResolver.resolve(clusters, "EOD", 100.0, "BULL")
    assert resolved[0].consensus_price == 110.0

def test_conflict_resolver_eod_bear():
    clusters = [
        ClusteredTarget(cluster_id=0, consensus_price=105.0, score=10, candidates=[]),
        ClusteredTarget(cluster_id=1, consensus_price=110.0, score=8, candidates=[])
    ]
    # EOD BEAR should pick highest score
    resolved = ConflictResolver.resolve(clusters, "EOD", 100.0, "BEAR")
    assert resolved[0].consensus_price == 105.0
