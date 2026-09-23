"""
app/forward_holdout_pipeline.py
AUTOMATED FORWARD-HOLDOUT RECORDING PIPELINE FOR MOMENTUM_THRUST_REVERSAL_H0

Specification Version: v1.2.0-CANONICAL-GOVERNANCE
Captures all 22 mandatory telemetry fields:
  1. signal_id
  2. symbol
  3. entry_timestamp
  4. entry_price
  5. pullback_low
  6. stop_price
  7. T1_price
  8. T2_price
  9. D1_high
  10. D1_low
  11. D1_close
  12. D2_high
  13. D2_low
  14. D2_close
  15. exit_reason (T1_HIT, T2_HIT, SL_HIT, GAP_SL_HIT, TIMEOUT)
  16. exit_price
  17. R_result
  18. MFE & MAE
  19. timeout_flag
  20. premature_exit_flag
  21. NIFTY_regime & VIX_regime
  22. slippage_assumption, transaction_cost, gap_through_stop_flag
"""

import os
import json
import sqlite3
from typing import Dict, Any, List, Optional
from dataclasses import asdict
import pandas as pd
import numpy as np

try:
    from momentum_thrust_h0_engine import H0Signal, H0TradeOutcome, MomentumThrustH0Engine
except ImportError:
    from app.momentum_thrust_h0_engine import H0Signal, H0TradeOutcome, MomentumThrustH0Engine

DEFAULT_DB_PATH = "data/forward_holdout/h0_holdout_audit.db"
DEFAULT_JSON_PATH = "data/forward_holdout/h0_holdout_trades.json"


class ForwardHoldoutPipeline:
    """Manages forward-holdout logging and telemetry persistence for H0."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH, json_path: str = DEFAULT_JSON_PATH):
        self.db_path = db_path
        self.json_path = json_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.json_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS h0_holdout_trades (
                signal_id TEXT PRIMARY KEY,
                symbol TEXT,
                entry_timestamp TEXT,
                entry_price REAL,
                pullback_low REAL,
                stop_price REAL,
                t1_price REAL,
                t2_price REAL,
                d1_high REAL,
                d1_low REAL,
                d1_close REAL,
                d2_high REAL,
                d2_low REAL,
                d2_close REAL,
                exit_reason TEXT,
                exit_price REAL,
                r_result REAL,
                mfe REAL,
                mae REAL,
                timeout_flag INTEGER,
                premature_exit_flag INTEGER,
                nifty_regime TEXT,
                vix_regime TEXT,
                slippage_assumption REAL,
                transaction_cost REAL,
                gap_through_stop_flag INTEGER,
                net_r_t1 REAL,
                net_r_t2 REAL,
                net_r_t3 REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    @staticmethod
    def calculate_transaction_friction(entry_price: float, exit_price: float, shares: int,
                                       tier: str = "T1") -> Dict[str, float]:
        """
        Calculates transaction friction according to the 4 frozen tiers:
          T0 = zero cost
          T1 = realistic (0.05% slippage per side + STT + turnover + GST)
          T2 = 1.5x realistic
          T3 = 2.0x realistic (stress test tier)
        """
        if tier == "T0":
            return {"slippage_per_side": 0.0, "total_cost_rupees": 0.0, "effective_exit_price": exit_price}

        # Multipliers
        mult = 1.0 if tier == "T1" else (1.5 if tier == "T2" else 2.0)

        # Slippage: 0.05% per side
        slip_rate = 0.0005 * mult
        slip_cost = (entry_price * slip_rate + exit_price * slip_rate) * shares

        # Statutory & Brokerage (NSE Delivery/Cash standard):
        # STT = 0.1% on buy & sell (delivery), Turnover ~0.003%, SEBI + Stamp ~0.005%, Brokerage flat ₹20 or 0.03%
        statutory_rate = (0.0010 + 0.00003 + 0.00005) * mult
        statutory_cost = (entry_price + exit_price) * shares * statutory_rate
        flat_brokerage = 40.0 * mult  # ₹20 buy + ₹20 sell

        total_cost = slip_cost + statutory_cost + flat_brokerage
        effective_exit = exit_price - (slip_rate * exit_price)

        return {
            "slippage_per_side": 0.05 * mult,
            "total_cost_rupees": round(total_cost, 2),
            "effective_exit_price": round(effective_exit, 2)
        }

    def record_trade_telemetry(self, outcome: H0TradeOutcome,
                               nifty_regime: str = "UNKNOWN",
                               vix_regime: str = "UNKNOWN") -> Dict[str, Any]:
        """Records a completed trade into the telemetry audit log across all 4 friction tiers."""
        shares = 100 # Standard sizing unit for logging
        t0 = self.calculate_transaction_friction(outcome.entry_price, outcome.exit_price, shares, "T0")
        t1 = self.calculate_transaction_friction(outcome.entry_price, outcome.exit_price, shares, "T1")
        t2 = self.calculate_transaction_friction(outcome.entry_price, outcome.exit_price, shares, "T2")
        t3 = self.calculate_transaction_friction(outcome.entry_price, outcome.exit_price, shares, "T3")

        risk = outcome.risk_per_share
        net_r_t1 = (outcome.pnl_per_share - (t1["total_cost_rupees"] / shares)) / risk if risk > 0 else outcome.r_multiple
        net_r_t2 = (outcome.pnl_per_share - (t2["total_cost_rupees"] / shares)) / risk if risk > 0 else outcome.r_multiple
        net_r_t3 = (outcome.pnl_per_share - (t3["total_cost_rupees"] / shares)) / risk if risk > 0 else outcome.r_multiple

        record = {
            "signal_id": outcome.signal_id,
            "symbol": outcome.symbol,
            "entry_timestamp": outcome.entry_date,
            "entry_price": outcome.entry_price,
            "pullback_low": round(outcome.entry_price * (1.0 - outcome.risk_pct / 100.0), 2),
            "stop_price": outcome.stop_price,
            "t1_price": outcome.t1_price,
            "t2_price": outcome.t2_price,
            "d1_high": outcome.d1_high,
            "d1_low": outcome.d1_low,
            "d1_close": outcome.d1_close,
            "d2_high": outcome.d2_high,
            "d2_low": outcome.d2_low,
            "d2_close": outcome.d2_close,
            "exit_reason": outcome.exit_reason,
            "exit_price": outcome.exit_price,
            "r_result": outcome.r_multiple,
            "mfe": outcome.mfe_pct,
            "mae": outcome.mae_pct,
            "timeout_flag": 1 if outcome.timeout_flag else 0,
            "premature_exit_flag": 1 if outcome.premature_exit_flag else 0,
            "nifty_regime": nifty_regime,
            "vix_regime": vix_regime,
            "slippage_assumption": t1["slippage_per_side"],
            "transaction_cost": t1["total_cost_rupees"],
            "gap_through_stop_flag": 1 if outcome.gap_through_stop else 0,
            "net_r_t1": round(net_r_t1, 4),
            "net_r_t2": round(net_r_t2, 4),
            "net_r_t3": round(net_r_t3, 4),
        }

        # Insert into SQLite
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO h0_holdout_trades (
                signal_id, symbol, entry_timestamp, entry_price, pullback_low, stop_price,
                t1_price, t2_price, d1_high, d1_low, d1_close, d2_high, d2_low, d2_close,
                exit_reason, exit_price, r_result, mfe, mae, timeout_flag, premature_exit_flag,
                nifty_regime, vix_regime, slippage_assumption, transaction_cost,
                gap_through_stop_flag, net_r_t1, net_r_t2, net_r_t3
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record["signal_id"], record["symbol"], record["entry_timestamp"], record["entry_price"],
            record["pullback_low"], record["stop_price"], record["t1_price"], record["t2_price"],
            record["d1_high"], record["d1_low"], record["d1_close"], record["d2_high"], record["d2_low"], record["d2_close"],
            record["exit_reason"], record["exit_price"], record["r_result"], record["mfe"], record["mae"],
            record["timeout_flag"], record["premature_exit_flag"], record["nifty_regime"], record["vix_regime"],
            record["slippage_assumption"], record["transaction_cost"], record["gap_through_stop_flag"],
            record["net_r_t1"], record["net_r_t2"], record["net_r_t3"]
        ))
        conn.commit()
        conn.close()

        return record

    def load_all_records(self) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM h0_holdout_trades ORDER BY entry_timestamp ASC")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
