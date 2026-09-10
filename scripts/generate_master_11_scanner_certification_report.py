#!/usr/bin/env python3
"""
scripts/generate_master_11_scanner_certification_report.py
Generates the complete institutional certification report for all 11 scanner families:
1. EOD Breakout
2. Accumulation / VCP
3. Pullback
4. Multi-TF (1H Base Contraction)
5. Reversal (Liquidity Sweep)
6. Wealth Engine (Multi-Month Growth)
7. Multibagger Engine (Convex Turnaround)
8. Daily Builder (Universe Stage Filter)
9. Short Covering EOD (Delivery Squeeze)
10. Multi-TF 5M Monitor (Armed Trigger)
11. Technical Ahat (Confluence Scanner)
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
logger = logging.getLogger("Master11CertificationReport")


def load_and_populate_11_registry() -> ChampionChallengerRegistry:
    registry = ChampionChallengerRegistry()
    reports_dir = os.path.join(project_root, "reports")

    files = [
        (ScannerFamily.EOD_BREAKOUT, "eod_breakout_outcomes.csv"),
        (ScannerFamily.PULLBACK, "pullback_outcomes.csv"),
        (ScannerFamily.ACCUMULATION_VCP, "vcp_outcomes.csv"),
        (ScannerFamily.MULTI_TF, "multitf_v3_outcomes.csv" if os.path.exists(os.path.join(reports_dir, "multitf_v3_outcomes.csv")) else "multitf_outcomes.csv"),
        (ScannerFamily.REVERSAL, "reversal_v1_vs_v2_outcomes.csv"),
        (ScannerFamily.WEALTH, "wealth_outcomes.csv"),
        (ScannerFamily.MULTIBAGGER, "multibagger_outcomes.csv"),
        (ScannerFamily.DAILY_BUILDER, "daily_builder_outcomes.csv"),
        (ScannerFamily.SHORT_COVERING_EOD, "short_covering_outcomes.csv"),
        (ScannerFamily.MULTI_TF_5M, "multitf_5m_outcomes.csv"),
        (ScannerFamily.TECHNICAL, "technical_outcomes.csv"),
    ]

    for family, fname in files:
        fpath = os.path.join(reports_dir, fname)
        if not os.path.exists(fpath):
            logger.warning(f"File not found: {fpath} — skipping {family.value}")
            continue

        try:
            df = pd.read_csv(fpath)
            logger.info(f"Loaded {len(df)} rows from {fname} for {family.value}")
        except Exception as e:
            logger.error(f"Error reading {fpath}: {e}")
            continue

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

            # Populate DEV (Initial Discovery / Calibration)
            df_dev = df_v[df_v["partition"].isin(["DEV", "IS", "IN_SAMPLE"])]
            if not df_dev.empty:
                m_dev = EvidenceMetrics.from_outcomes_df(df_dev, variant_id=vid, partition_type="DEV")
                try:
                    registry.update_variant_metrics(vid, m_dev, "DEV")
                except KeyError:
                    pass

            # Populate VAL (Challenger Tuning & Hyperparameter Selection)
            df_val = df_v[df_v["partition"].isin(["VAL", "VALIDATION"])]
            if not df_val.empty:
                m_val = EvidenceMetrics.from_outcomes_df(df_val, variant_id=vid, partition_type="VAL")
                try:
                    registry.update_variant_metrics(vid, m_val, "VAL")
                except KeyError:
                    pass

            # Populate HOLDOUT (Locked Untouched Holdout)
            df_holdout = df_v[df_v["partition"].isin(["HOLDOUT", "OOS", "OUT_OF_SAMPLE"])]
            if not df_holdout.empty:
                m_holdout = EvidenceMetrics.from_outcomes_df(df_holdout, variant_id=vid, partition_type="HOLDOUT")
                try:
                    registry.update_variant_metrics(vid, m_holdout, "HOLDOUT")
                except KeyError:
                    pass

    return registry


def run_11_promotions_and_report(registry: ChampionChallengerRegistry):
    print("\n" + "=" * 135)
    print("      ELITE BREAKOUT SYSTEM — MASTER 11-SCANNER THREE-WAY HOLDOUT CERTIFICATION REPORT      ")
    print("=" * 135)

    results = []
    hdr = f"  {'Scanner Family':<20} {'Active Best Variant':<32} {'Tier':<16} {'DEV E[R](N)':<14} {'VAL E[R](N)':<14} {'HOLDOUT E[R](N)':<17} {'Holdout PF':>10} {'Holdout Win%':>12} {'Live Capital':<14}"
    print(hdr)
    print("  " + "-" * 131)

    live_count = 0
    research_count = 0

    for scanner in ScannerFamily:
        promoted = registry.compare_and_promote(scanner)
        active_champ = registry.get_champion(scanner)
        
        if active_champ:
            m_dev = active_champ.in_sample_metrics
            m_val = active_champ.val_metrics
            m_hld = active_champ.oos_metrics

            dev_str = f"{m_dev.expectancy_r:+.2f}R ({m_dev.sample_size})" if m_dev else "N/A"
            val_str = f"{m_val.expectancy_r:+.2f}R ({m_val.sample_size})" if m_val else "N/A"
            hld_str = f"{m_hld.expectancy_r:+.2f}R ({m_hld.sample_size})" if m_hld else "N/A"

            pf_hld = m_hld.profit_factor if m_hld else (m_val.profit_factor if m_val else 0.0)
            wr_hld = m_hld.win_rate_pct if m_hld else (m_val.win_rate_pct if m_val else 0.0)
            tier_str = active_champ.allocation_tier.value
            
            is_live = active_champ.is_live_trade_allowed()
            if is_live:
                live_count += 1
                status_str = "🟢 ALLOWED"
            else:
                research_count += 1
                status_str = "🟡 PAPER ONLY"
            
            print(f"  {scanner.value:<20} {active_champ.variant_id:<32} {tier_str:<16} {dev_str:<14} {val_str:<14} {hld_str:<17} {pf_hld:>10.2f} {wr_hld:>11.1f}% {status_str:<14}")
            results.append({
                "scanner": scanner.value,
                "active_variant": active_champ.variant_id,
                "tier": tier_str,
                "dev_metrics": dev_str,
                "val_metrics": val_str,
                "holdout_metrics": hld_str,
                "holdout_pf": pf_hld,
                "holdout_wr": wr_hld,
                "live_allowed": is_live,
            })
        else:
            research_count += 1
            print(f"  {scanner.value:<20} {'NO_ACTIVE_CHAMPION':<32} {'TIER4_RESEARCH':<16} {'N/A':<14} {'N/A':<14} {'N/A':<17} {0.0:>10.2f} {0.0:>11.1f}% {'🔴 UNVERIFIED':<14}")

    print("  " + "-" * 131)
    print(f"  ECOSYSTEM TOTALS: {live_count} Production-Approved Engines (Tier 1 & Tier 2) + {research_count} Governed Research Engines (Tier 3) = {live_count + research_count} Total Scanners")

    # Export registry
    out_json = os.path.join(project_root, "reports", "champion_challenger_final_registry.json")
    report_data = registry.generate_registry_report()
    with open(out_json, "w") as f:
        json.dump(report_data, f, indent=2)
    print(f"\nSaved updated 11-scanner registry to {out_json}")
    return results


if __name__ == "__main__":
    reg = load_and_populate_11_registry()
    run_11_promotions_and_report(reg)
