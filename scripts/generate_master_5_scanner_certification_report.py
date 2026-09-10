"""
scripts/generate_master_5_scanner_certification_report.py
Generates the complete institutional certification report by loading all 5 scanner
historical outcomes, populating the ChampionChallengerRegistry, executing promotion
evaluations, and exporting the final registry snapshot.
"""

import sys
import os
import json
import logging
from datetime import datetime, timezone
import pandas as pd
import numpy as np

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
app_dir = os.path.join(project_root, "app")
sys.path.insert(0, app_dir)
sys.path.insert(1, project_root)

from champion_challenger_registry import (
    ChampionChallengerRegistry,
    ScannerFamily,
    EvidenceMetrics,
    VariantStatus,
    AllocationTier,
    ScannerVariant,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("CertificationReport")


def load_and_populate_registry() -> ChampionChallengerRegistry:
    registry = ChampionChallengerRegistry()
    reports_dir = os.path.join(project_root, "reports")

    # File mapping: ScannerFamily -> (csv_filename, variant_col, partition_col)
    files = [
        (ScannerFamily.EOD_BREAKOUT, "eod_breakout_outcomes.csv"),
        (ScannerFamily.PULLBACK, "pullback_outcomes.csv"),
        (ScannerFamily.ACCUMULATION_VCP, "vcp_outcomes.csv"),
        (ScannerFamily.MULTI_TF, "multitf_v3_outcomes.csv" if os.path.exists(os.path.join(reports_dir, "multitf_v3_outcomes.csv")) else "multitf_outcomes.csv"),
        (ScannerFamily.REVERSAL, "reversal_v1_vs_v2_outcomes.csv"),
    ]

    for family, fname in files:
        fpath = os.path.join(reports_dir, fname)
        if not os.path.exists(fpath):
            logger.warning(f"File not found: {fpath} — skipping {family.value}")
            continue

        df = pd.read_csv(fpath)
        logger.info(f"Loaded {len(df)} rows from {fname}")

        # Each CSV contains 'variant_id' and 'partition'
        variants = df["variant_id"].unique()
        for vid in variants:
            df_v = df[df["variant_id"] == vid]
            
            if registry.get_variant(vid) is None:
                registry.register_variant(
                    ScannerVariant(
                        variant_id=vid,
                        scanner_family=family,
                        description=f"{family.value} variant {vid}",
                        parameters={},
                        status=VariantStatus.CHAMPION if "CHAMPION" in vid else VariantStatus.CHALLENGER,
                    )
                )

            # Populate IN_SAMPLE
            df_is = df_v[df_v["partition"].isin(["IS", "IN_SAMPLE"])]
            if not df_is.empty:
                m_is = EvidenceMetrics.from_outcomes_df(df_is, variant_id=vid, partition_type="IN_SAMPLE")
                try:
                    registry.update_variant_metrics(vid, m_is, "IN_SAMPLE")
                except KeyError:
                    pass

            # Populate OUT_OF_SAMPLE
            df_oos = df_v[df_v["partition"].isin(["OOS", "OUT_OF_SAMPLE"])]
            if not df_oos.empty:
                m_oos = EvidenceMetrics.from_outcomes_df(df_oos, variant_id=vid, partition_type="OUT_OF_SAMPLE")
                try:
                    registry.update_variant_metrics(vid, m_oos, "OUT_OF_SAMPLE")
                except KeyError:
                    pass

    return registry


def run_promotions_and_report(registry: ChampionChallengerRegistry):
    print("\n" + "=" * 80)
    print("      ELITE BREAKOUT SYSTEM — FINAL CHAMPION / CHALLENGER CERTIFICATION      ")
    print("=" * 80)

    results = []
    for scanner in ScannerFamily:
        promoted = registry.compare_and_promote(scanner)
        policy = registry.get_live_policy(scanner)
        champ = registry.get_champion(scanner)
        
        m_oos = champ.oos_metrics if champ else None
        m_is = champ.in_sample_metrics if champ else None
        best_m = m_oos or m_is

        results.append({
            "scanner": scanner.value,
            "champion_variant": champ.variant_id if champ else "NONE",
            "tier": policy["tier"],
            "live_trade_allowed": policy["live_trade_allowed"],
            "oos_n": m_oos.sample_size if m_oos else 0,
            "oos_expectancy_r": m_oos.expectancy_r if m_oos else 0.0,
            "oos_pf": m_oos.profit_factor if m_oos else 0.0,
            "oos_win_rate": m_oos.win_rate_pct if m_oos else 0.0,
            "r2_conversion": m_oos.r2_hit_rate_pct if m_oos else 0.0,
            "post_sl_recovery": m_oos.post_sl_recovery_pct if m_oos else 0.0,
            "p_value": m_oos.approx_p_value if m_oos else None,
            "ci_95_lower": m_oos.ci_95_lower_r if m_oos else 0.0,
            "ci_95_upper": m_oos.ci_95_upper_r if m_oos else 0.0,
        })

    df_summary = pd.DataFrame(results)
    print("\n" + df_summary.to_string(index=False))
    print("\n" + "=" * 80)

    # Save to json report
    out_path = os.path.join(project_root, "reports", "champion_challenger_final_registry.json")
    with open(out_path, "w") as f:
        json.dump(registry.generate_registry_report(), f, indent=2)
    logger.info(f"Exported final registry state to {out_path}")

    return df_summary


if __name__ == "__main__":
    reg = load_and_populate_registry()
    run_promotions_and_report(reg)
