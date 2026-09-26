#!/usr/bin/env python3
"""
build_master_certification_file.py
Consolidates all certification ledgers, summaries, gate decompositions, and scanner
specifications into a single master CSV ledger and a single master markdown document.
"""

import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CERT_DIR = ROOT / "reports" / "certification"


def build_master_csv():
    records = []

    # 1. EOD
    eod_path = CERT_DIR / "EOD" / "ledger.csv"
    if eod_path.exists():
        df_eod = pd.read_csv(eod_path)
        for _, row in df_eod.iterrows():
            gates = {
                "trend_stack": row.get("gate_trend_stack"),
                "rsi_corridor": row.get("gate_rsi_corridor"),
                "atr_tightness": row.get("gate_atr_tightness"),
                "rvol_floor": row.get("gate_rvol_floor"),
                "52w_proximity": row.get("gate_52w_proximity"),
                "obv_slope": row.get("gate_obv_slope"),
                "triple_fault_veto": row.get("gate_triple_fault_veto"),
            }
            records.append(
                {
                    "scanner": "EOD",
                    "evaluation_type": "HISTORICAL_REPLAY",
                    "symbol": row.get("symbol"),
                    "signal_timestamp": row.get("signal_timestamp"),
                    "signal_regime": row.get("signal_regime"),
                    "entry_timestamp": row.get("entry_timestamp"),
                    "entry_price": row.get("entry_price"),
                    "stop_loss": row.get("stop_loss"),
                    "target_1": row.get("target_1"),
                    "target_2": row.get("target_2"),
                    "target_3": row.get("target_3"),
                    "target_4": row.get("target_4"),
                    "exit_timestamp": row.get("exit_timestamp"),
                    "exit_price": row.get("exit_price"),
                    "exit_reason": row.get("exit_reason"),
                    "r_multiple": row.get("r_multiple"),
                    "holding_period_bars_or_days": row.get(
                        "holding_period_bars_or_days"
                    ),
                    "composite_score": row.get("composite_score"),
                    "gate_details": json.dumps(gates),
                    "naive_baseline_fired": row.get("naive_baseline_fired"),
                    "data_source_flags": row.get("data_source_flags"),
                }
            )

    # 2. TECHNICAL_INTRADAY
    tech_path = CERT_DIR / "TECHNICAL_INTRADAY" / "ledger.csv"
    if tech_path.exists():
        df_tech = pd.read_csv(tech_path)
        for _, row in df_tech.iterrows():
            gates = {
                "pattern_type": row.get("gate_pattern_type"),
                "clv_floor": row.get("gate_clv_floor"),
                "rvol_floor": row.get("gate_rvol_floor"),
                "upper_wick": row.get("gate_upper_wick"),
                "risk_pct": row.get("gate_risk_pct"),
            }
            records.append(
                {
                    "scanner": "TECHNICAL_INTRADAY",
                    "evaluation_type": "HISTORICAL_REPLAY",
                    "symbol": row.get("symbol"),
                    "signal_timestamp": row.get("signal_timestamp"),
                    "signal_regime": row.get("signal_regime"),
                    "entry_timestamp": row.get("entry_timestamp"),
                    "entry_price": row.get("entry_price"),
                    "stop_loss": row.get("stop_loss"),
                    "target_1": row.get("target_1"),
                    "target_2": row.get("target_2"),
                    "target_3": row.get("target_3"),
                    "target_4": row.get("target_4"),
                    "exit_timestamp": row.get("exit_timestamp"),
                    "exit_price": row.get("exit_price"),
                    "exit_reason": row.get("exit_reason"),
                    "r_multiple": row.get("r_multiple"),
                    "holding_period_bars_or_days": row.get(
                        "holding_period_bars_or_days"
                    ),
                    "composite_score": row.get("composite_score"),
                    "gate_details": json.dumps(gates),
                    "naive_baseline_fired": row.get("naive_baseline_fired"),
                    "data_source_flags": row.get("data_source_flags"),
                }
            )

    # 3. MULTI_TF (15M)
    mtf_path = CERT_DIR / "MULTI_TF" / "ledger.csv"
    if mtf_path.exists():
        df_mtf = pd.read_csv(mtf_path)
        for _, row in df_mtf.iterrows():
            gates = {
                "1h_trend": row.get("gate_1h_trend"),
                "30m_bbwp_squeeze": row.get("gate_30m_bbwp_squeeze"),
                "15m_thrust": row.get("gate_15m_thrust"),
                "diurnal_rvol": row.get("gate_diurnal_rvol"),
            }
            records.append(
                {
                    "scanner": "MULTI_TF",
                    "evaluation_type": "HISTORICAL_REPLAY",
                    "symbol": row.get("symbol"),
                    "signal_timestamp": row.get("signal_timestamp"),
                    "signal_regime": row.get("signal_regime"),
                    "entry_timestamp": row.get("entry_timestamp"),
                    "entry_price": row.get("entry_price"),
                    "stop_loss": row.get("stop_loss"),
                    "target_1": row.get("target_1"),
                    "target_2": row.get("target_2"),
                    "target_3": row.get("target_3"),
                    "target_4": row.get("target_4"),
                    "exit_timestamp": row.get("exit_timestamp"),
                    "exit_price": row.get("exit_price"),
                    "exit_reason": row.get("exit_reason"),
                    "r_multiple": row.get("r_multiple"),
                    "holding_period_bars_or_days": row.get(
                        "holding_period_bars_or_days"
                    ),
                    "composite_score": row.get("composite_score"),
                    "gate_details": json.dumps(gates),
                    "naive_baseline_fired": row.get("naive_baseline_fired"),
                    "data_source_flags": row.get("data_source_flags"),
                }
            )

    # 4. MULTI_TF_5M
    m5m_path = CERT_DIR / "MULTI_TF_5M" / "ledger.csv"
    if m5m_path.exists():
        df_m5m = pd.read_csv(m5m_path)
        for _, row in df_m5m.iterrows():
            gates = {
                "5m_atr_momentum": row.get("gate_5m_atr_momentum"),
                "vwap_extension": row.get("gate_vwap_extension"),
                "consolidation_coil": row.get("gate_consolidation_coil"),
                "volume_ignition": row.get("gate_volume_ignition"),
            }
            records.append(
                {
                    "scanner": "MULTI_TF_5M",
                    "evaluation_type": "HISTORICAL_REPLAY",
                    "symbol": row.get("symbol"),
                    "signal_timestamp": row.get("signal_timestamp"),
                    "signal_regime": row.get("signal_regime"),
                    "entry_timestamp": row.get("entry_timestamp"),
                    "entry_price": row.get("entry_price"),
                    "stop_loss": row.get("stop_loss"),
                    "target_1": row.get("target_1"),
                    "target_2": row.get("target_2"),
                    "target_3": row.get("target_3"),
                    "target_4": row.get("target_4"),
                    "exit_timestamp": row.get("exit_timestamp"),
                    "exit_price": row.get("exit_price"),
                    "exit_reason": row.get("exit_reason"),
                    "r_multiple": row.get("r_multiple"),
                    "holding_period_bars_or_days": row.get(
                        "holding_period_bars_or_days"
                    ),
                    "composite_score": row.get("composite_score"),
                    "gate_details": json.dumps(gates),
                    "naive_baseline_fired": row.get("naive_baseline_fired"),
                    "data_source_flags": row.get("data_source_flags"),
                }
            )

    # 5. Live Triage
    live_path = CERT_DIR / "phase1_live_triage" / "live_alerts_ledger.csv"
    if live_path.exists():
        df_live = pd.read_csv(live_path)
        for _, row in df_live.iterrows():
            records.append(
                {
                    "scanner": row.get("scanner") or row.get("category"),
                    "evaluation_type": "LIVE_TRIAGE",
                    "symbol": row.get("symbol"),
                    "signal_timestamp": row.get("alert_timestamp"),
                    "signal_regime": "LIVE_MARKET",
                    "entry_timestamp": row.get("alert_timestamp"),
                    "entry_price": row.get("entry_price"),
                    "stop_loss": row.get("stop_loss"),
                    "target_1": row.get("target_1"),
                    "target_2": np.nan,
                    "target_3": np.nan,
                    "target_4": np.nan,
                    "exit_timestamp": row.get("exit_timestamp"),
                    "exit_price": row.get("exit_price"),
                    "exit_reason": row.get("status"),
                    "r_multiple": row.get("r_multiple"),
                    "holding_period_bars_or_days": np.nan,
                    "composite_score": np.nan,
                    "gate_details": json.dumps(
                        {"source": row.get("source"), "status": row.get("status")}
                    ),
                    "naive_baseline_fired": np.nan,
                    "data_source_flags": "LIVE_DB",
                }
            )

    master_df = pd.DataFrame(records)
    out_csv = CERT_DIR / "MASTER_ALL_SCANNERS_LEDGER.csv"
    master_df.to_csv(out_csv, index=False)
    print(f"Master CSV written: {out_csv} ({len(master_df)} rows)")
    return master_df


if __name__ == "__main__":
    build_master_csv()
