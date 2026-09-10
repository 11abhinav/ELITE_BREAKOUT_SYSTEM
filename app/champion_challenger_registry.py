# =====================================================================================
# app/champion_challenger_registry.py
# INSTITUTIONAL CHAMPION / CHALLENGER SCANNER OPTIMIZATION REGISTRY
# =====================================================================================
#
# PURPOSE
# -------
# Implements the "Fix All Five" doctrine:
#   - No scanner is retired because its baseline underperforms.
#   - Every parameter configuration is treated as an explicit challenger hypothesis.
#   - A challenger replaces the champion ONLY when it demonstrates statistically
#     significant AND economically meaningful improvement WITHOUT unacceptable
#     regression elsewhere.
#
# LIFECYCLE
# ---------
#   Scanner -> generate candidates -> improve selection -> improve timing
#   -> improve SL/targets -> backtest -> Champion vs Challenger compare
#   -> OOS walk-forward validate -> Minimum Evidence Package check -> promote
#
# ALLOCATION TIERS (all criteria must be met simultaneously)
# ----------------------------------------------------------
#   Tier 1 Proven    : E[R] >= +0.30R, PF >= 1.50, p < 0.05, OOS stable
#   Tier 2 Validated : E[R] >= +0.15R, PF >= 1.30, positive >= 2 regimes
#   Tier 3 Candidate : 0.00R <= E[R] < +0.15R  OR  N < 50 (watchlist only)
#   Tier 4 Research  : E[R] < 0.00R or regime-negative (sandbox replay only)
#
# THRESHOLDS ARE CANDIDATE PARAMETERS — NOT HARD-CODED TRUTHS.
# They can themselves be subjected to walk-forward sensitivity analysis.
# =====================================================================================

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("champion_challenger_registry")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ScannerFamily(str, Enum):
    EOD_BREAKOUT       = "EOD_BREAKOUT"
    MULTI_TF           = "MULTI_TF"
    PULLBACK           = "PULLBACK"
    REVERSAL           = "REVERSAL"
    ACCUMULATION_VCP   = "ACCUMULATION_VCP"
    WEALTH             = "WEALTH"
    MULTIBAGGER        = "MULTIBAGGER"
    DAILY_BUILDER      = "DAILY_BUILDER"
    SHORT_COVERING_EOD = "SHORT_COVERING_EOD"
    MULTI_TF_5M        = "MULTI_TF_5M"
    TECHNICAL          = "TECHNICAL"


class AllocationTier(str, Enum):
    TIER1_PROVEN    = "TIER1_PROVEN"       # Full live capital
    TIER2_VALIDATED = "TIER2_VALIDATED"    # Standard live capital
    TIER3_CANDIDATE = "TIER3_CANDIDATE"    # Watchlist / restricted
    TIER4_RESEARCH  = "TIER4_RESEARCH"     # Sandbox replay only


class VariantStatus(str, Enum):
    CHAMPION   = "CHAMPION"    # Current production configuration
    CHALLENGER = "CHALLENGER"  # Under evaluation
    PROMOTED   = "PROMOTED"    # Beat champion; now new champion
    DEFEATED   = "DEFEATED"    # Tested; did not beat champion
    RETIRED    = "RETIRED"     # Intentionally superseded (kept for audit)


# ---------------------------------------------------------------------------
# Minimum Evidence Package
# ---------------------------------------------------------------------------

@dataclass
class MinimumEvidencePackage:
    """
    All 7 criteria must be satisfied simultaneously for tier promotion.
    Thresholds are CANDIDATE parameters — not hard-coded truths.
    """
    tier1_expectancy_r:  float = 0.30
    tier1_profit_factor: float = 1.50
    tier1_min_sample_n:  int   = 50
    tier1_ci_lower_r:    float = 0.05  # Bootstrap 95% CI lower bound
    tier1_oos_regimes:   int   = 2     # Positive in >= N independent regimes
    tier1_p_value:       float = 0.05

    tier2_expectancy_r:  float = 0.15
    tier2_profit_factor: float = 1.30
    tier2_min_sample_n:  int   = 30
    tier2_oos_regimes:   int   = 2

    tier3_expectancy_r:  float = 0.00
    tier3_min_sample_n:  int   = 15

    def evaluate(self, metrics: "EvidenceMetrics") -> AllocationTier:
        """Determines allocation tier from an EvidenceMetrics snapshot."""
        n       = metrics.sample_size
        er      = metrics.expectancy_r
        pf      = metrics.profit_factor
        ci_lo   = metrics.ci_95_lower_r
        regimes = metrics.positive_regime_count
        p_val   = metrics.approx_p_value

        if (n >= self.tier1_min_sample_n
                and er >= self.tier1_expectancy_r
                and pf >= self.tier1_profit_factor
                and ci_lo >= self.tier1_ci_lower_r
                and regimes >= self.tier1_oos_regimes
                and (p_val is None or p_val <= self.tier1_p_value)):
            return AllocationTier.TIER1_PROVEN

        if (n >= self.tier2_min_sample_n
                and er >= self.tier2_expectancy_r
                and pf >= self.tier2_profit_factor
                and regimes >= self.tier2_oos_regimes):
            return AllocationTier.TIER2_VALIDATED

        if n >= self.tier3_min_sample_n and er >= self.tier3_expectancy_r:
            return AllocationTier.TIER3_CANDIDATE

        return AllocationTier.TIER4_RESEARCH


# ---------------------------------------------------------------------------
# Evidence Metrics
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    """Standard normal CDF approximation via math.erfc."""
    return 0.5 * math.erfc(-x / math.sqrt(2))


@dataclass
class EvidenceMetrics:
    """
    Captures the full R-distribution for one variant over one evaluation partition.
    All fields are candidates for walk-forward stability comparison.
    """
    variant_id:            str
    evaluation_period:     str              = ""
    partition_type:        str              = "IN_SAMPLE"  # IN_SAMPLE | OOS | WALK_FORWARD

    sample_size:           int              = 0
    expectancy_r:          float            = 0.0
    median_r:              float            = 0.0
    profit_factor:         float            = 0.0
    win_rate_pct:          float            = 0.0
    avg_win_r:             float            = 0.0
    avg_loss_r:            float            = 0.0

    # Quality ladder hit rates
    r1_hit_rate_pct:       float            = 0.0
    r1_5_hit_rate_pct:     float            = 0.0
    r2_hit_rate_pct:       float            = 0.0
    r3_hit_rate_pct:       float            = 0.0
    r5_hit_rate_pct:       float            = 0.0
    r10_hit_rate_pct:      float            = 0.0

    # Excursion profiles
    avg_mfe_r:             float            = 0.0
    avg_mae_r:             float            = 0.0
    post_sl_recovery_pct:  float            = 0.0  # % of stopped trades that recovered to entry

    # Statistical confidence
    ci_95_lower_r:         float            = 0.0
    ci_95_upper_r:         float            = 0.0
    approx_p_value:        Optional[float]  = None

    # Regime breakdown
    regime_breakdown:      Dict[str, float] = field(default_factory=dict)
    positive_regime_count: int              = 0

    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def from_outcomes_df(
        cls,
        df: pd.DataFrame,
        variant_id: str,
        partition_type: str = "IN_SAMPLE",
        evaluation_period: str = "",
        block_size: int = 5,
        n_bootstraps: int = 1000,
        random_seed: int = 42
    ) -> "EvidenceMetrics":
        """
        Computes all metrics from a raw outcomes DataFrame.

        Expected columns: realized_rr, r1_hit_before_sl, r1_5_hit_before_sl,
                          r2_hit_before_sl, r3_hit_before_sl, r5_hit_before_sl,
                          r10_hit_before_sl, max_favorable_excursion_r,
                          max_adverse_excursion_r, post_sl_recovered_entry, regime.
        """
        m = cls(variant_id=variant_id, partition_type=partition_type,
                evaluation_period=evaluation_period)

        if df is None or df.empty:
            return m

        r_vals = df["realized_rr"].dropna().astype(float).values
        n = len(r_vals)
        if n == 0:
            return m

        m.sample_size  = n
        m.expectancy_r = float(np.mean(r_vals))
        m.median_r     = float(np.median(r_vals))

        wins   = r_vals[r_vals > 0]
        losses = r_vals[r_vals <= 0]

        m.win_rate_pct = round(len(wins) / n * 100.0, 1)
        m.avg_win_r    = round(float(wins.mean()),        2) if len(wins)   > 0 else 0.0
        m.avg_loss_r   = round(float(abs(losses.mean())), 2) if len(losses) > 0 else 0.0

        gross_profit = float(wins.sum())        if len(wins)   > 0 else 0.0
        gross_loss   = float(abs(losses.sum())) if len(losses) > 0 else 0.0
        m.profit_factor = (
            round(gross_profit / gross_loss, 2) if gross_loss > 0
            else (99.0 if gross_profit > 0 else 0.0)
        )

        def _hit(col: str) -> float:
            return round(float(df[col].sum()) / n * 100.0, 1) if col in df.columns else 0.0

        m.r1_hit_rate_pct   = _hit("r1_hit_before_sl")
        m.r1_5_hit_rate_pct = _hit("r1_5_hit_before_sl")
        m.r2_hit_rate_pct   = _hit("r2_hit_before_sl")
        m.r3_hit_rate_pct   = _hit("r3_hit_before_sl")
        m.r5_hit_rate_pct   = _hit("r5_hit_before_sl")
        m.r10_hit_rate_pct  = _hit("r10_hit_before_sl")

        if "max_favorable_excursion_r" in df.columns:
            m.avg_mfe_r = round(float(df["max_favorable_excursion_r"].mean()), 2)
        if "max_adverse_excursion_r" in df.columns:
            m.avg_mae_r = round(float(df["max_adverse_excursion_r"].mean()), 2)
        if "post_sl_recovered_entry" in df.columns:
            m.post_sl_recovery_pct = round(float(df["post_sl_recovered_entry"].mean()) * 100.0, 1)

        # Block-Bootstrap 95% CI (Vectorized NumPy for high performance)
        rng = np.random.default_rng(random_seed)
        if n <= 1000:
            num_blocks = max(1, n // block_size)
            starts = rng.integers(0, max(1, n - block_size + 1), size=(n_bootstraps, num_blocks))
            offsets = np.arange(block_size)
            idx_matrix = (starts[:, :, None] + offsets[None, None, :]).reshape(n_bootstraps, -1)[:, :n]
            idx_matrix = np.clip(idx_matrix, 0, n - 1)
            boot_means = r_vals[idx_matrix].mean(axis=1)
        else:
            idx_matrix = rng.integers(0, n, size=(n_bootstraps, min(n, 2000)))
            boot_means = r_vals[idx_matrix].mean(axis=1)

        if len(boot_means) > 0:
            m.ci_95_lower_r = round(float(np.percentile(boot_means, 2.5)),  3)
            m.ci_95_upper_r = round(float(np.percentile(boot_means, 97.5)), 3)

        # Approximate t-test p-value (H0: E[R] = 0, Gaussian approx for large N)
        if n > 1:
            se = float(np.std(r_vals, ddof=1)) / math.sqrt(n)
            if se > 0:
                t_stat = m.expectancy_r / se
                m.approx_p_value = round(2.0 * (1.0 - _norm_cdf(abs(t_stat))), 4)

        # Regime breakdown
        if "regime" in df.columns:
            rd = df.groupby("regime")["realized_rr"].mean()
            m.regime_breakdown = {k: round(float(v), 2) for k, v in rd.items()}
            m.positive_regime_count = sum(1 for v in m.regime_breakdown.values() if v > 0)

        return m

    def summary_dict(self) -> Dict[str, Any]:
        """JSON-serialisable summary for dashboards and audit."""
        return {
            "variant_id":           self.variant_id,
            "partition":            self.partition_type,
            "period":               self.evaluation_period,
            "N":                    self.sample_size,
            "E[R]":                 round(self.expectancy_r, 2),
            "median_R":             round(self.median_r, 2),
            "profit_factor":        self.profit_factor,
            "win_rate_pct":         self.win_rate_pct,
            "CI_95":                [self.ci_95_lower_r, self.ci_95_upper_r],
            "p_value":              self.approx_p_value,
            "+1R_pct":              self.r1_hit_rate_pct,
            "+1.5R_pct":            self.r1_5_hit_rate_pct,
            "+2R_pct":              self.r2_hit_rate_pct,
            "+3R_pct":              self.r3_hit_rate_pct,
            "+5R_pct":              self.r5_hit_rate_pct,
            "+10R_pct":             self.r10_hit_rate_pct,
            "avg_MFE_R":            self.avg_mfe_r,
            "avg_MAE_R":            self.avg_mae_r,
            "post_SL_recovery_pct": self.post_sl_recovery_pct,
            "positive_regimes":     self.positive_regime_count,
            "regime_breakdown":     self.regime_breakdown,
        }


# ---------------------------------------------------------------------------
# Scanner Variant
# ---------------------------------------------------------------------------

@dataclass
class ScannerVariant:
    """
    A single configuration of a scanner (champion or challenger).
    Parameters are stored as-declared and are NOT assumed to be optimal
    until empirically verified through the full evaluation lifecycle.
    """
    variant_id:           str
    scanner_family:       ScannerFamily
    description:          str
    parameters:           Dict[str, Any]
    status:               VariantStatus           = VariantStatus.CHALLENGER
    allocation_tier:      AllocationTier          = AllocationTier.TIER4_RESEARCH
    in_sample_metrics:    Optional[EvidenceMetrics] = None
    val_metrics:          Optional[EvidenceMetrics] = None
    oos_metrics:          Optional[EvidenceMetrics] = None
    walk_forward_metrics: List[EvidenceMetrics]   = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    evaluated_at: Optional[str] = None
    notes: str = ""

    def update_tier(self, evidence_pkg: MinimumEvidencePackage) -> AllocationTier:
        """Re-evaluates tier using locked holdout metrics if available, else val, else in-sample."""
        best = self.oos_metrics or self.val_metrics or self.in_sample_metrics
        if best is None:
            self.allocation_tier = AllocationTier.TIER4_RESEARCH
            return self.allocation_tier
        self.allocation_tier = evidence_pkg.evaluate(best)
        return self.allocation_tier

    def is_live_trade_allowed(self) -> bool:
        return self.allocation_tier in (AllocationTier.TIER1_PROVEN, AllocationTier.TIER2_VALIDATED)

    def summary(self) -> Dict[str, Any]:
        best = self.oos_metrics or self.in_sample_metrics
        return {
            "variant_id":     self.variant_id,
            "scanner":        self.scanner_family.value,
            "status":         self.status.value,
            "tier":           self.allocation_tier.value,
            "live_allowed":   self.is_live_trade_allowed(),
            "parameters":     self.parameters,
            "description":    self.description,
            "best_metrics":   best.summary_dict() if best else None,
            "oos_partitions": len(self.walk_forward_metrics),
        }


# ---------------------------------------------------------------------------
# Pre-registered variants — 22 total across 5 scanner families
# ---------------------------------------------------------------------------

_BUILT_IN_VARIANTS: List[Dict[str, Any]] = [
    # ── EOD BREAKOUT ──────────────────────────────────────────────────────────
    dict(variant_id="EOD_CHAMPION_V1", scanner_family=ScannerFamily.EOD_BREAKOUT,
         description="Baseline EOD Breakout champion (E[R]=+0.19R, PF=1.38, +2R=29.2%)",
         parameters={"prior_bar_lookback": "iloc[-21:-1]", "vol_confirmation": True},
         status=VariantStatus.CHAMPION),
    dict(variant_id="EOD_CHALL_A_THRUST", scanner_family=ScannerFamily.EOD_BREAKOUT,
         description="Challenger A: Breakout thrust > 0.50 x ATR filter",
         parameters={"min_breakout_thrust_atr": 0.50, "prior_bar_lookback": "iloc[-21:-1]"},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="EOD_CHALL_B_SCORE80", scanner_family=ScannerFamily.EOD_BREAKOUT,
         description="Challenger B: Quality score gate >= 80 (up from 70)",
         parameters={"min_quality_score": 80, "prior_bar_lookback": "iloc[-21:-1]"},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="EOD_CHALL_C_EXTENSION", scanner_family=ScannerFamily.EOD_BREAKOUT,
         description="Challenger C: Max extension <= 2.5% from pivot at entry",
         parameters={"max_pivot_extension_pct": 2.5, "prior_bar_lookback": "iloc[-21:-1]"},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="EOD_CHALL_D_THRUST_030", scanner_family=ScannerFamily.EOD_BREAKOUT,
         description="Challenger D: Moderate breakout thrust > 0.30 x ATR filter",
         parameters={"min_breakout_thrust_atr": 0.30, "prior_bar_lookback": "iloc[-21:-1]"},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="EOD_CHALL_E_COMBINED", scanner_family=ScannerFamily.EOD_BREAKOUT,
         description="Challenger E: Composite filter (Thrust >= 0.30 ATR + Extension <= 2.5% + Score >= 75)",
         parameters={"min_breakout_thrust_atr": 0.30, "max_pivot_extension_pct": 2.5, "min_quality_score": 75,
                     "prior_bar_lookback": "iloc[-21:-1]"},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="EOD_CHALL_F_CALIBRATED_SCORE", scanner_family=ScannerFamily.EOD_BREAKOUT,
         description="Challenger F: Calibrated score with late-extension penalty (>2.5% past pivot = -15 pts)",
         parameters={"extension_penalty": True, "max_unpenalized_ext": 2.5, "penalty_pts": 15},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="EOD_CHALL_G_SWEET_SPOT", scanner_family=ScannerFamily.EOD_BREAKOUT,
         description="Challenger G: Sweet-spot gate (Score 70-79 or 90-100, excludes uncalibrated 80-89)",
         parameters={"allowed_score_bands": [(70, 79), (90, 100)]},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="EOD_CHALL_H_THRUST_CALIBRATED", scanner_family=ScannerFamily.EOD_BREAKOUT,
         description="Challenger H: Calibrated Thrust >= 0.40 ATR + Extension <= 2.5%",
         parameters={"min_breakout_thrust_atr": 0.40, "max_pivot_extension_pct": 2.5},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="EOD_CHALL_I_MONOTONIC_RUBRIC", scanner_family=ScannerFamily.EOD_BREAKOUT,
         description="Challenger I: Score formula-level recalibration with direct extension penalty",
         parameters={"formula_extension_penalty": True},
         status=VariantStatus.CHALLENGER),

    # ── MULTI-TF ──────────────────────────────────────────────────────────────
    dict(variant_id="MULTITF_CHAMPION_V1", scanner_family=ScannerFamily.MULTI_TF,
         description="Baseline Multi-TF champion (E[R]=+0.10R, PF=1.22, closed-candle)",
         parameters={"h1_closed_candle": True, "m15_lookback": "iloc[-17:-1]"},
         status=VariantStatus.CHAMPION),
    dict(variant_id="MULTITF_CHALL_A_DAILY", scanner_family=ScannerFamily.MULTI_TF,
         description="Challenger A: Require H1 + Daily trend alignment",
         parameters={"require_daily_alignment": True, "h1_closed_candle": True},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="MULTITF_CHALL_B_CONSOLIDATION", scanner_family=ScannerFamily.MULTI_TF,
         description="Challenger B: M15 consolidation tightness < 1.5 x ATR",
         parameters={"max_m15_range_atr": 1.5, "h1_closed_candle": True},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="MULTITF_CHALL_C_REGIME", scanner_family=ScannerFamily.MULTI_TF,
         description="Challenger C: Regime filter — Bull/Neutral only",
         parameters={"allowed_regimes": ["BULL", "NEUTRAL"], "h1_closed_candle": True},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="MULTITF_CHALL_D_EXT_22", scanner_family=ScannerFamily.MULTI_TF,
         description="Challenger D: Relaxed extension gate <= 2.2 ATR",
         parameters={"max_extension_atr": 2.2, "h1_closed_candle": True},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="MULTITF_CHALL_E_EXT_25", scanner_family=ScannerFamily.MULTI_TF,
         description="Challenger E: Relaxed extension gate <= 2.5 ATR",
         parameters={"max_extension_atr": 2.5, "h1_closed_candle": True},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="MULTITF_CHALL_F_EXT_30", scanner_family=ScannerFamily.MULTI_TF,
         description="Challenger F: Wide extension gate <= 3.0 ATR",
         parameters={"max_extension_atr": 3.0, "h1_closed_candle": True},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="MULTITF_CHALL_G_COMPOSITE", scanner_family=ScannerFamily.MULTI_TF,
         description="Challenger G: Composite relaxed funnel (Ext <= 2.5 ATR, Wick <= 0.35, Close pos >= 0.65)",
         parameters={"max_extension_atr": 2.5, "max_upper_wick": 0.35, "min_close_pos": 0.65, "h1_closed_candle": True},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="MULTITF_CHALL_H_TIGHT_BASE", scanner_family=ScannerFamily.MULTI_TF,
         description="Challenger H: Pre-breakout H1/M15 contraction <= 1.2 ATR + Early Ignition (Ext <= 0.8 ATR) + Structural SL",
         parameters={"pre_breakout_contraction": True, "max_extension_atr": 0.80, "structural_stop": True},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="MULTITF_CHALL_I_EARLY_IGNITION", scanner_family=ScannerFamily.MULTI_TF,
         description="Challenger I: Challenger H + Bull/Neutral Regime alignment",
         parameters={"pre_breakout_contraction": True, "max_extension_atr": 0.80, "structural_stop": True, "allowed_regimes": ["BULL", "NEUTRAL"]},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="MULTITF_V3A_STANDARD", scanner_family=ScannerFamily.MULTI_TF,
         description="V3 Standard: 20-bar intraday base <= 1.8 ATR + contraction <= 0.80 + volume >= 1.5x",
         parameters={"base_bars": 20, "max_base_atr": 1.80, "max_comp": 0.80},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="MULTITF_V3B_TIGHT_VCP", scanner_family=ScannerFamily.MULTI_TF,
         description="V3 Tight VCP: 20-bar intraday base <= 1.4 ATR + contraction <= 0.70 + volume >= 1.8x",
         parameters={"base_bars": 20, "max_base_atr": 1.40, "max_comp": 0.70},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="MULTITF_V3C_MODERATE", scanner_family=ScannerFamily.MULTI_TF,
         description="V3 Moderate: 15-bar base <= 2.0 ATR + contraction <= 0.85 + volume >= 1.3x",
         parameters={"base_bars": 15, "max_base_atr": 2.00, "max_comp": 0.85},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="MULTITF_V3D_BULL_ONLY", scanner_family=ScannerFamily.MULTI_TF,
         description="V3 Bull Only: Standard base breakout filtered strictly to Macro Bull regimes",
         parameters={"base_bars": 20, "allowed_regimes": ["BULL"]},
         status=VariantStatus.CHALLENGER),

    # ── PULLBACK ──────────────────────────────────────────────────────────────
    dict(variant_id="PULLBACK_CHAMPION_V1", scanner_family=ScannerFamily.PULLBACK,
         description="Baseline Pullback champion (45.8% post-SL recovery — stop suffocation hypothesis)",
         parameters={"stop_model": "FIXED_PCT", "stop_pct": 5.0},
         status=VariantStatus.CHAMPION),
    dict(variant_id="PULLBACK_CHALL_A_1_5ATR", scanner_family=ScannerFamily.PULLBACK,
         description="Challenger A: Stop = 1.5 x ATR_14",
         parameters={"stop_model": "ATR_MULTIPLE", "atr_period": 14, "atr_multiple": 1.5},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="PULLBACK_CHALL_B_1_8ATR", scanner_family=ScannerFamily.PULLBACK,
         description="Challenger B: Stop = 1.8 x ATR_14, clamped [4.5%, 7.5%]",
         parameters={"stop_model": "ATR_CLAMPED", "atr_period": 14, "atr_multiple": 1.8,
                     "clamp_min_pct": 4.5, "clamp_max_pct": 7.5},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="PULLBACK_CHALL_C_2ATR_SHELF", scanner_family=ScannerFamily.PULLBACK,
         description="Challenger C: Stop = 2.0 x ATR_14 + anchored under swing pivot shelf",
         parameters={"stop_model": "ATR_SWING_SHELF", "atr_period": 14, "atr_multiple": 2.0,
                     "swing_shelf_bars": 5},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="PULLBACK_CHALL_D_ADAPTIVE", scanner_family=ScannerFamily.PULLBACK,
         description="Challenger D: Volatility-regime adaptive stop",
         parameters={"stop_model": "VOLATILITY_ADAPTIVE", "vol_regime_col": "atr_pctile"},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="PULLBACK_CHALL_E_CONFIRMED_TURN", scanner_family=ScannerFamily.PULLBACK,
         description="Challenger E: Volatility-adaptive stop + price action turn-up candle confirmation",
         parameters={"stop_model": "VOLATILITY_ADAPTIVE", "confirmation_candle": True},
         status=VariantStatus.CHALLENGER),

    # ── REVERSAL ──────────────────────────────────────────────────────────────
    dict(variant_id="REVERSAL_CHAMPION_V1", scanner_family=ScannerFamily.REVERSAL,
         description="Baseline Reversal V1 (E[R]=+0.03R, PF=1.06 — raw oversold detection)",
         parameters={"rsi_threshold": 35, "min_drop_pct": 12.0},
         status=VariantStatus.CHAMPION),
    dict(variant_id="REVERSAL_V2A_SWEEP_RECLAIM", scanner_family=ScannerFamily.REVERSAL,
         description="Challenger V2-A: Liquidity sweep + price reclaim above breakdown level",
         parameters={"stage": "SWEEP_RECLAIM", "min_sweep_depth_pct": 0.3,
                     "reclaim_confirmation_bars": 1},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="REVERSAL_V2B_DISPLACEMENT", scanner_family=ScannerFamily.REVERSAL,
         description="Challenger V2-B: V2-A + Bullish displacement candle",
         parameters={"stage": "SWEEP_RECLAIM_DISPLACEMENT", "min_body_ratio": 0.50,
                     "min_close_position": 0.75},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="REVERSAL_V2C_HIGHER_LOW", scanner_family=ScannerFamily.REVERSAL,
         description="Challenger V2-C: V2-B + structural Higher Low confirmation",
         parameters={"stage": "FULL_STRUCTURE", "require_higher_low": True},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="REVERSAL_V2D_FULL_FUNNEL", scanner_family=ScannerFamily.REVERSAL,
         description="Challenger V2-D: Full 5-stage funnel + macro regime + R:R >= 2.5",
         parameters={"stage": "FULL_FUNNEL", "min_rr_ratio": 2.5,
                     "allowed_regimes": ["BULL", "NEUTRAL"], "require_higher_low": True},
         status=VariantStatus.CHALLENGER),

    # ── REVERSAL V2.1 MULTI-BAR PERSISTENCE
    dict(variant_id="REVERSAL_V21A_SWEEP_RECLAIM", scanner_family=ScannerFamily.REVERSAL,
         description="Challenger V2.1-A: Multi-bar (1-5 bars) sweep persistence + price reclaim above swept level",
         parameters={"stage": "SWEEP_RECLAIM_MULTIBAR", "reclaim_window_bars": 5, "min_sweep_depth_pct": 0.3},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="REVERSAL_V21B_DISPLACEMENT", scanner_family=ScannerFamily.REVERSAL,
         description="Challenger V2.1-B: V2.1-A + Bullish displacement candle on reclaim bar",
         parameters={"stage": "SWEEP_RECLAIM_DISPLACEMENT_MULTIBAR", "reclaim_window_bars": 5,
                     "min_body_ratio": 0.50, "min_close_position": 0.70},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="REVERSAL_V21C_HIGHER_LOW", scanner_family=ScannerFamily.REVERSAL,
         description="Challenger V2.1-C: V2.1-B + structural Higher Low confirmation",
         parameters={"stage": "FULL_STRUCTURE_MULTIBAR", "reclaim_window_bars": 5, "require_higher_low": True},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="REVERSAL_V21D_FULL_FUNNEL", scanner_family=ScannerFamily.REVERSAL,
         description="Challenger V2.1-D: Full 5-stage multi-bar funnel + macro regime + R:R >= 2.5",
         parameters={"stage": "FULL_FUNNEL_MULTIBAR", "reclaim_window_bars": 5, "min_rr_ratio": 2.5,
                     "allowed_regimes": ["BULL", "NEUTRAL"], "require_higher_low": True},
         status=VariantStatus.CHALLENGER),

    # ── REVERSAL V2.2 MACRO REGIME & CAPITULATION HYPOTHESES
    dict(variant_id="REVERSAL_V22A_NO_BEAR", scanner_family=ScannerFamily.REVERSAL,
         description="Challenger V2.2-A: V2.1 Multi-bar sweep + Macro Bear regime exclusion (Bull/Neutral only)",
         parameters={"stage": "SWEEP_RECLAIM_MULTIBAR", "reclaim_window_bars": 5, "allowed_regimes": ["BULL", "NEUTRAL"]},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="REVERSAL_V22B_CAPITULATION", scanner_family=ScannerFamily.REVERSAL,
         description="Challenger V2.2-B: V2.1 Multi-bar + Panic capitulation volume (RVOL >= 2.0x or RSI < 30)",
         parameters={"stage": "SWEEP_RECLAIM_MULTIBAR", "min_capitulation_rvol": 2.0, "max_rsi": 30.0},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="REVERSAL_V22C_WIDER_SL", scanner_family=ScannerFamily.REVERSAL,
         description="Challenger V2.2-C: V2.1 Multi-bar + Buffered Stop Loss (Seq Low - 0.5x ATR)",
         parameters={"stage": "SWEEP_RECLAIM_MULTIBAR", "sl_buffer_atr": 0.5},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="REVERSAL_V22D_COMPOSITE", scanner_family=ScannerFamily.REVERSAL,
         description="Challenger V2.2-D: Composite (Buffered SL + No-Bear + Capitulation Volume)",
         parameters={"stage": "SWEEP_RECLAIM_MULTIBAR", "sl_buffer_atr": 0.5, "allowed_regimes": ["BULL", "NEUTRAL"], "min_capitulation_rvol": 1.5},
         status=VariantStatus.CHALLENGER),

    # ── ACCUMULATION / VCP ───────────────────────────────────────────────────
    dict(variant_id="VCP_CHAMPION_V1", scanner_family=ScannerFamily.ACCUMULATION_VCP,
         description="Baseline VCP champion (E[R]=+0.57R, PF=2.35, +2R=43.4% — strongest edge)",
         parameters={"vol_dry_up_ratio": None, "base_tightness_pct": None},
         status=VariantStatus.CHAMPION),
    dict(variant_id="VCP_CHALL_A_VOL_DRYUP", scanner_family=ScannerFamily.ACCUMULATION_VCP,
         description="Challenger A: Require volume dry-up < 0.40 x SMA20",
         parameters={"vol_dry_up_ratio": 0.40},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="VCP_CHALL_B_TIGHTNESS", scanner_family=ScannerFamily.ACCUMULATION_VCP,
         description="Challenger B: Base tightness span < 12%",
         parameters={"base_tightness_pct": 12.0},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="VCP_CHALL_C_COMBINED", scanner_family=ScannerFamily.ACCUMULATION_VCP,
         description="Challenger C: Combined dry-up < 0.40 + tightness < 12% + RS breakout",
         parameters={"vol_dry_up_ratio": 0.40, "base_tightness_pct": 12.0,
                     "require_rs_breakout": True},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="VCP_CHALL_D_T3_MODERATE", scanner_family=ScannerFamily.ACCUMULATION_VCP,
         description="Challenger D: Moderate contraction tightness (T3 <= 8.0%)",
         parameters={"max_t3_tightness_pct": 8.0},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="VCP_CHALL_E_EXPANSION_THRUST", scanner_family=ScannerFamily.ACCUMULATION_VCP,
         description="Challenger E: Moderate tightness T3 <= 8.0% + Breakout Volume >= 1.5x SMA20",
         parameters={"max_t3_tightness_pct": 8.0, "min_thrust_vol_ratio": 1.5},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="VCP_CHALL_F_REGIME_ALIGNED", scanner_family=ScannerFamily.ACCUMULATION_VCP,
         description="Challenger F: VCP Baseline + Macro Regime filter (BULL and NEUTRAL only)",
         parameters={"allowed_regimes": ["BULL", "NEUTRAL"]},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="VCP_CHALL_G_RUNNER_EXPANSION", scanner_family=ScannerFamily.ACCUMULATION_VCP,
         description="Challenger G: Moderate T3 <= 8.0% + Volume Thrust >= 1.8x in Bull/Neutral Regimes",
         parameters={"max_t3_tightness_pct": 8.0, "min_thrust_vol_ratio": 1.8, "allowed_regimes": ["BULL", "NEUTRAL"]},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="VCP_CHALL_H_TIGHT_THRUST", scanner_family=ScannerFamily.ACCUMULATION_VCP,
         description="Challenger H: Tight T3 <= 7.0% + Thrust >= 1.5x + Score >= 75",
         parameters={"max_t3_tightness_pct": 7.0, "min_thrust_vol_ratio": 1.5, "min_score": 75.0},
         status=VariantStatus.CHALLENGER),

    # -----------------------------------------------------------------------
    # 6. WEALTH ENGINE (Long-Term Compounding & Momentum)
    # -----------------------------------------------------------------------
    dict(variant_id="WEALTH_CHAMPION_V1", scanner_family=ScannerFamily.WEALTH,
         description="Wealth Baseline: High ROE + Sales Growth + Momentum Filter",
         parameters={"min_roe": 15.0, "min_sales_growth": 12.0, "rs_threshold": 70},
         status=VariantStatus.CHAMPION),
    dict(variant_id="WEALTH_CHALL_A_QUALITY_MOMENTUM", scanner_family=ScannerFamily.WEALTH,
         description="Challenger A: ROE >= 20% + Low Debt-to-Equity + RS >= 75 + 200DMA Alignment",
         parameters={"min_roe": 20.0, "max_de": 0.5, "rs_threshold": 75, "above_200dma": True},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="WEALTH_CHALL_B_STAGE2_CONVEXITY", scanner_family=ScannerFamily.WEALTH,
         description="Challenger B: Stage 2 Growth + Trailing 50DMA Stop + Quarterly Earnings Acceleration",
         parameters={"stage": 2, "trailing_exit": "50DMA", "earnings_accel": True},
         status=VariantStatus.CHALLENGER),

    # -----------------------------------------------------------------------
    # 7. MULTIBAGGER ENGINE (Convex Asymmetry & Turnarounds)
    # -----------------------------------------------------------------------
    dict(variant_id="MULTIBAGGER_CHAMPION_V1", scanner_family=ScannerFamily.MULTIBAGGER,
         description="Multibagger Baseline: Micro/Mid-Cap Small Base Expansion + EPS Growth Spike",
         parameters={"market_cap_max_cr": 10000, "eps_growth_min": 25.0},
         status=VariantStatus.CHAMPION),
    dict(variant_id="MULTIBAGGER_CHALL_A_CONVEX_CATALYST", scanner_family=ScannerFamily.MULTIBAGGER,
         description="Challenger A: Structural Turnaround + Institutional Accumulation + Volatility Coil",
         parameters={"promoter_holding_min": 50.0, "pledge_max": 5.0, "vol_dry_up": True},
         status=VariantStatus.CHALLENGER),
    dict(variant_id="MULTIBAGGER_CHALL_B_EXPANSION_SCALE", scanner_family=ScannerFamily.MULTIBAGGER,
         description="Challenger B: Industry Leader + Multi-Year Base Breakout + High Relative Strength",
         parameters={"multi_year_breakout": True, "min_rs": 80, "trailing_exit": "20EMA"},
         status=VariantStatus.CHALLENGER),

    # -----------------------------------------------------------------------
    # 8. DAILY BUILDER (Pre-Filter Universe & Base Construction)
    # -----------------------------------------------------------------------
    dict(variant_id="DAILY_BUILDER_CHAMPION_V1", scanner_family=ScannerFamily.DAILY_BUILDER,
         description="Daily Builder Baseline: Vectorized 5-tier universe quality & stage scoring",
         parameters={"min_price": 20.0, "min_turnover_cr": 1.0},
         status=VariantStatus.CHAMPION),
    dict(variant_id="DAILY_BUILDER_CHALL_A_PRISTINE_BASE", scanner_family=ScannerFamily.DAILY_BUILDER,
         description="Challenger A: Stage 1B/2A Strict Base Filter + Zero Overhead Supply",
         parameters={"stage_filter": ["1B", "2A"], "overhead_resistance_pct": 5.0},
         status=VariantStatus.CHALLENGER),

    # -----------------------------------------------------------------------
    # 9. SHORT COVERING EOD (Delivery & Squeeze Ignition)
    # -----------------------------------------------------------------------
    dict(variant_id="SHORT_COVERING_CHAMPION_V1", scanner_family=ScannerFamily.SHORT_COVERING_EOD,
         description="Short Covering Baseline: Delivery Volume Spike + Heavy Capitulation Low Rebound",
         parameters={"delivery_spike_mult": 2.0, "rsi_oversold": 35.0},
         status=VariantStatus.CHAMPION),
    dict(variant_id="SHORT_COVERING_CHALL_A_SQUEEZE_CONFIRMED", scanner_family=ScannerFamily.SHORT_COVERING_EOD,
         description="Challenger A: High Delivery % + Break Above 5-Day High + Open Interest Reduction",
         parameters={"delivery_spike_mult": 2.5, "confirm_swing_break": True},
         status=VariantStatus.CHALLENGER),

    # -----------------------------------------------------------------------
    # 10. MULTI-TF 5M MONITOR (Intraday Armed Micro-Breakout)
    # -----------------------------------------------------------------------
    dict(variant_id="MULTITF_5M_CHAMPION_V1", scanner_family=ScannerFamily.MULTI_TF_5M,
         description="5M Monitor Baseline: 5m Candle Surge breaking 15m Consolidation Ceiling",
         parameters={"candle_timeframe": "5m", "rvol_5m_min": 2.0},
         status=VariantStatus.CHAMPION),
    dict(variant_id="MULTITF_5M_CHALL_A_TIGHT_COIL_IGNITION", scanner_family=ScannerFamily.MULTI_TF_5M,
         description="Challenger A: 5M Opening Range Breakout + RVOL >= 3.0x + VWAP Alignment",
         parameters={"rvol_5m_min": 3.0, "above_vwap": True, "max_opening_range_atr": 1.2},
         status=VariantStatus.CHALLENGER),

    # -----------------------------------------------------------------------
    # 11. TECHNICAL AHAT (Pattern & Confluence Alpha Scanner)
    # -----------------------------------------------------------------------
    dict(variant_id="TECHNICAL_CHAMPION_V1", scanner_family=ScannerFamily.TECHNICAL,
         description="Technical Baseline: Classical Chart Pattern + Multi-Indicator Confluence",
         parameters={"min_confluence_score": 70, "adx_min": 20.0},
         status=VariantStatus.CHAMPION),
    dict(variant_id="TECHNICAL_CHALL_A_HIGH_CONFLUENCE", scanner_family=ScannerFamily.TECHNICAL,
         description="Challenger A: High Confluence Score >= 80 + Supertrend Alignment + MACD Expansion",
         parameters={"min_confluence_score": 80, "supertrend_bull": True, "macd_hist_positive": True},
         status=VariantStatus.CHALLENGER),
]


# ---------------------------------------------------------------------------
# Champion / Challenger Registry
# ---------------------------------------------------------------------------

class ChampionChallengerRegistry:
    """
    Central registry managing variant lifecycle across all five alpha engines.

    The winner of any champion/challenger contest earns the production slot ONLY
    when it demonstrates statistically and economically meaningful improvement
    without unacceptable regression in tail payoff or regime stability.

    Usage:
        registry = ChampionChallengerRegistry()
        metrics = EvidenceMetrics.from_outcomes_df(df, variant_id="PULLBACK_CHALL_B_1_8ATR")
        registry.update_variant_metrics("PULLBACK_CHALL_B_1_8ATR", metrics, "OOS")
        winner = registry.compare_and_promote(ScannerFamily.PULLBACK)
        print(winner.summary())
    """

    def __init__(self, evidence_pkg: Optional[MinimumEvidencePackage] = None):
        self._evidence_pkg = evidence_pkg or MinimumEvidencePackage()
        self._variants: Dict[str, ScannerVariant] = {}
        self._initialize_built_in_variants()

    def _initialize_built_in_variants(self) -> None:
        for cfg in _BUILT_IN_VARIANTS:
            v = ScannerVariant(
                variant_id=cfg["variant_id"],
                scanner_family=cfg["scanner_family"],
                description=cfg["description"],
                parameters=cfg["parameters"],
                status=cfg.get("status", VariantStatus.CHALLENGER),
            )
            self._variants[v.variant_id] = v

    # -- Access ---------------------------------------------------------------

    def get_variant(self, variant_id: str) -> Optional[ScannerVariant]:
        return self._variants.get(variant_id)

    def get_champion(self, scanner: ScannerFamily) -> Optional[ScannerVariant]:
        for v in self._variants.values():
            if v.scanner_family == scanner and v.status == VariantStatus.CHAMPION:
                return v
        return None

    def get_challengers(self, scanner: ScannerFamily) -> List[ScannerVariant]:
        return [v for v in self._variants.values()
                if v.scanner_family == scanner and v.status == VariantStatus.CHALLENGER]

    def get_all_variants(self, scanner: Optional[ScannerFamily] = None) -> List[ScannerVariant]:
        variants = list(self._variants.values())
        if scanner:
            variants = [v for v in variants if v.scanner_family == scanner]
        return variants

    def register_variant(self, variant: ScannerVariant) -> None:
        if variant.variant_id in self._variants:
            raise ValueError(f"Variant {variant.variant_id!r} already registered.")
        self._variants[variant.variant_id] = variant

    # -- Metrics ingestion ----------------------------------------------------

    def update_variant_metrics(
        self,
        variant_id: str,
        metrics: EvidenceMetrics,
        partition_type: str = "IN_SAMPLE"
    ) -> None:
        """
        Ingests evaluation results into the variant record.
        Supports IN_SAMPLE, OOS, and WALK_FORWARD partitions.
        """
        v = self._variants.get(variant_id)
        if v is None:
            raise KeyError(f"Variant {variant_id!r} not found. Register it first.")

        if partition_type in ("DEV", "IN_SAMPLE", "IS"):
            v.in_sample_metrics = metrics
        elif partition_type in ("VAL", "VALIDATION"):
            v.val_metrics = metrics
        elif partition_type in ("HOLDOUT", "OOS", "OUT_OF_SAMPLE"):
            v.oos_metrics = metrics
        elif partition_type == "WALK_FORWARD":
            v.walk_forward_metrics.append(metrics)
        else:
            raise ValueError(f"Unknown partition_type {partition_type!r}")

        v.update_tier(self._evidence_pkg)
        v.evaluated_at = datetime.now(timezone.utc).isoformat()
        logger.info(
            "Variant %s [%s]: E[R]=%.2f PF=%.2f Tier=%s",
            variant_id, partition_type, metrics.expectancy_r,
            metrics.profit_factor, v.allocation_tier.value
        )

    # -- Champion/Challenger comparison ---------------------------------------

    def compare_and_promote(self, scanner: ScannerFamily) -> Optional[ScannerVariant]:
        """
        Promotes the best challenger if it beats the champion on ALL of:
          1. Higher E[R] (OOS preferred over IS)
          2. Higher profit factor
          3. No material R-tail regression (< 10% tolerance on +2R hit rate)
          4. Minimum sample size met
          5. CI delta lower bound > -0.10R

        Returns the current champion (possibly updated to new variant).
        """
        champion = self.get_champion(scanner)
        if champion is None:
            logger.warning("No champion for %s — cannot compare.", scanner.value)
            return None

        champ_m     = champion.oos_metrics or champion.val_metrics or champion.in_sample_metrics
        challengers = self.get_challengers(scanner)
        if not challengers:
            return champion

        best_challenger: Optional[ScannerVariant] = None
        best_delta_e: float = 0.0

        for c in challengers:
            cm = c.oos_metrics or c.val_metrics or c.in_sample_metrics
            if cm is None:
                logger.debug("Challenger %s has no metrics — skipping.", c.variant_id)
                continue

            if champ_m is None:
                # Champion has no metrics yet; first evaluated challenger wins
                best_challenger, best_delta_e = c, cm.expectancy_r
                continue

            delta_e     = cm.expectancy_r   - champ_m.expectancy_r
            delta_pf    = cm.profit_factor  - champ_m.profit_factor
            delta_ci_lo = cm.ci_95_lower_r  - champ_m.ci_95_lower_r   # Lower bound regression guard
            n_ok        = cm.sample_size >= self._evidence_pkg.tier3_min_sample_n
            no_r2_regr  = cm.r2_hit_rate_pct >= (champ_m.r2_hit_rate_pct * 0.90)  # <10% regression

            is_better = (
                delta_e > 0
                and delta_pf > 0
                and delta_ci_lo > -0.10
                and n_ok
                and no_r2_regr
            )

            logger.debug(
                "Challenger %s vs %s: dE=%.2f dPF=%.2f dCI=%.2f => better=%s",
                c.variant_id, champion.variant_id, delta_e, delta_pf, delta_ci_lo, is_better
            )

            if is_better and delta_e > best_delta_e:
                best_challenger, best_delta_e = c, delta_e

        if best_challenger is not None:
            logger.info(
                "PROMOTION: %s beats %s by +%.2fR on %s",
                best_challenger.variant_id, champion.variant_id,
                best_delta_e, scanner.value
            )
            champion.status        = VariantStatus.RETIRED
            best_challenger.status = VariantStatus.CHAMPION
            best_challenger.update_tier(self._evidence_pkg)
            return best_challenger

        logger.info(
            "Champion %s retained for %s — no challenger passed all gates.",
            champion.variant_id, scanner.value
        )
        return champion

    # -- Live gating ----------------------------------------------------------

    def get_live_policy(self, scanner: ScannerFamily) -> Dict[str, Any]:
        """
        Returns the current live gating policy for a scanner based on its
        champion's allocation tier. Used by strategy_policy and candidate_tracker.
        """
        champion = self.get_champion(scanner)
        if champion is None:
            return {
                "live_trade_allowed": False,
                "tier": AllocationTier.TIER4_RESEARCH.value,
                "reason": "No champion registered.",
            }
        return {
            "scanner":            scanner.value,
            "champion_variant":   champion.variant_id,
            "tier":               champion.allocation_tier.value,
            "live_trade_allowed": champion.is_live_trade_allowed(),
            "parameters":         champion.parameters,
            "description":        champion.description,
        }

    # -- Reporting ------------------------------------------------------------

    def generate_registry_report(self) -> List[Dict[str, Any]]:
        """Full registry dump for dashboards and forensic audit."""
        rows = []
        for scanner in ScannerFamily:
            champion    = self.get_champion(scanner)
            challengers = self.get_challengers(scanner)
            rows.append({
                "scanner":            scanner.value,
                "champion":           champion.summary() if champion else None,
                "active_challengers": len(challengers),
                "challengers":        [c.summary() for c in challengers],
            })
        return rows

    def generate_score_interaction_hypotheses(self) -> List[Dict[str, Any]]:
        """
        Scaffolds the 4 Score x Entry-condition interaction hypotheses.
        These are CANDIDATE explanations for why Score 85-89 outperforms 90+.
        Must be empirically tested — not assumed true.
        Results must survive apply_multiple_testing_correction() (Benjamini-Hochberg).
        """
        return [
            {
                "hypothesis_id":    "SCORE_H1_LATE_EXTENSION",
                "description":      "Score 90+ enters >3% past pivot (late-stage extension)",
                "test_type":        "INTERACTION_REGRESSION",
                "covariates":       ["score_bucket", "entry_extension_pct"],
                "interaction_term": "score_bucket x entry_extension_pct",
                "p_value":          None,   # Populated after regression
                "fdr_significant":  None,
            },
            {
                "hypothesis_id":    "SCORE_H2_EXPANSION_FATIGUE",
                "description":      "Score 90+ triggers after 4+ consecutive expansion candles",
                "test_type":        "INTERACTION_REGRESSION",
                "covariates":       ["score_bucket", "consecutive_expansion_candles"],
                "interaction_term": "score_bucket x consecutive_expansion_candles",
                "p_value":          None,
                "fdr_significant":  None,
            },
            {
                "hypothesis_id":    "SCORE_H3_COMPOSITION_BIAS",
                "description":      "Score 90+ disproportionately composed of momentum breakouts in Bear regime",
                "test_type":        "COMPOSITION_DECOMPOSITION",
                "covariates":       ["score_bucket", "scanner", "regime"],
                "interaction_term": "score_bucket x scanner x regime",
                "p_value":          None,
                "fdr_significant":  None,
            },
            {
                "hypothesis_id":    "SCORE_H4_SAMPLING_NOISE",
                "description":      "85-89 vs 90+ difference is within bootstrap sampling variance (N too small)",
                "test_type":        "BOOTSTRAP_VARIANCE_TEST",
                "covariates":       ["score_bucket"],
                "interaction_term": None,
                "p_value":          None,
                "fdr_significant":  None,
            },
        ]
