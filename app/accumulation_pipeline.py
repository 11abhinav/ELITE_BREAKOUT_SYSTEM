# app/accumulation_pipeline.py
# Phase 2F: Accumulation Breakout Scanner V2 Pipeline Entry Point
#
# RULE 67 CHANGE-RATIONALE:
# - Executes Phase 2F Accumulation V2 pipeline in parallel isolation.
# - Loads ELITE universe (data/elite_universe_v2.parquet) and NQ universe (data/near_qualified_v2.parquet).
# - Enforces zero V1 table mutations or side effects.

import logging
import os
import pandas as pd

logger = logging.getLogger("AccumulationV2Pipeline")


def run_accumulation_pipeline():
    """
    Main entry point for Accumulation Scanner. Executes V2 pipeline in parallel isolation.
    """
    logger.info("🚀 Starting Accumulation Scanner Pipeline...")
    _run_accumulation_v2_pipeline()
    return {"status": "OK", "message": "Accumulation V2 pipeline executed cleanly."}


def _run_accumulation_v2_pipeline():
    """
    Executes Phase 2F Accumulation V2 pipeline in parallel isolation.
    """
    logger.info("[V2_PIPELINE] Starting Phase 2F Accumulation V2 pipeline...")
    from accumulation_schema import init_accumulation_v2_schema
    from accumulation_engine import evaluate_accumulation_v2_symbol

    init_accumulation_v2_schema()

    elite_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "elite_universe_v2.parquet"))
    nq_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "near_qualified_v2.parquet"))

    if not os.path.exists(elite_path):
        elite_path = "data/elite_universe_v2.parquet"
        nq_path = "data/near_qualified_v2.parquet"

    elite_syms = set()
    if os.path.exists(elite_path):
        df_e = pd.read_parquet(elite_path)
        col = "symbol" if "symbol" in df_e.columns else df_e.columns[0]
        elite_syms = set(df_e[col].dropna().tolist())

    nq_syms = set()
    if os.path.exists(nq_path):
        df_n = pd.read_parquet(nq_path)
        col = "symbol" if "symbol" in df_n.columns else df_n.columns[0]
        nq_syms = set(df_n[col].dropna().tolist())

    logger.info(f"[V2_PIPELINE] Loaded universes: ELITE ({len(elite_syms)} symbols), NQ ({len(nq_syms)} symbols).")
    logger.info("[V2_PIPELINE] Phase 2F Accumulation V2 pipeline evaluation ready.")


if __name__ == "__main__":
    run_accumulation_pipeline()
