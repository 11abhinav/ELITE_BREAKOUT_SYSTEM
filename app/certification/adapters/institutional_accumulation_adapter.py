# app/certification/adapters/institutional_accumulation_adapter.py
"""
Institutional Smart Money Accumulation Scanner Certification Adapter (app/accumulation/scanner.py).
Executes the 12-step hard cascade sequence, piecewise sub-score normalization,
hard component gates, structural SL/Target calculations, and setup emission.
"""
import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional
import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ROOT_DIR = os.path.abspath(os.path.join(_APP_DIR, ".."))

from app.certification.models import (
    ProductionDecisionRecord,
    FrozenDataSnapshot,
    GateAuditResult,
    ReplayMode,
    ScannerType
)
from app.certification.provenance import (
    get_git_commit,
    get_file_hash,
    get_config_hash,
    get_dataframe_hash
)
from app.certification.adapters.base import BaseScannerAdapter


class InstitutionalAccumulationAdapter(BaseScannerAdapter):
    """Adapter for Institutional Smart Money Accumulation Scanner (app/accumulation/scanner.py)."""

    def __init__(self, scanner_type: ScannerType = ScannerType.INSTITUTIONAL_ACCUMULATION):
        super().__init__(scanner_type=scanner_type)

    def evaluate(
        self,
        symbol: str,
        evaluation_date: str,
        mode: ReplayMode = ReplayMode.CLEAN_HISTORICAL_REPLAY,
        prod_record: Optional[ProductionDecisionRecord] = None,
        custom_data: Optional[Dict[str, Any]] = None,
        effective_config: Optional[Dict[str, Any]] = None
    ) -> ProductionDecisionRecord:
        from app.accumulation.scanner import AccumulationScanner

        # 1. Load historical daily price data
        df_daily = None
        fund_data = None
        if custom_data:
            df_daily = custom_data.get("df")
            fund_data = custom_data.get("fund_data")

        if df_daily is None:
            p = os.path.join(_ROOT_DIR, "data", "history", "1d", f"{symbol}.parquet")
            if os.path.exists(p):
                df_daily = pd.read_parquet(p)

        if df_daily is None or df_daily.empty:
            return self._build_empty_record(symbol, evaluation_date, mode, "MISSING_HISTORICAL_DATA")

        # 2. Slice strictly up to evaluation date
        time_col = next((c for c in ["Datetime", "Date", "timestamp"] if c in df_daily.columns), None)
        if time_col:
            eval_dt_str = evaluation_date.split(" ")[0]
            dt_str_series = df_daily[time_col].astype(str).str[:10]
            df_daily = df_daily[dt_str_series <= eval_dt_str].copy()
        elif isinstance(df_daily.index, pd.DatetimeIndex):
            eval_dt_str = evaluation_date.split(" ")[0]
            dt_str_series = df_daily.index.astype(str).str[:10]
            df_daily = df_daily[dt_str_series <= eval_dt_str].copy()

        if len(df_daily) < 15:
            return self._build_empty_record(symbol, evaluation_date, mode, "INSUFFICIENT_BARS_FOR_ACCUMULATION")

        # 3. Invoke AccumulationScanner
        scanner = AccumulationScanner(run_id=f"ACCUM_REPLAY_{symbol}_{evaluation_date.split(' ')[0]}")
        fund_input = fund_data or {"roe": 18.0, "roce": 22.0, "de_ratio": 0.3}
        
        result = scanner.process_symbol(
            symbol=symbol,
            df=df_daily,
            fundamental_data=fund_input,
            delivery_status="VALID"
        )

        passed = bool(result.get("passed", False))
        reason = result.get("reason")
        decision = "SELECTED" if passed else "REJECTED"

        latest = df_daily.iloc[-1]
        close_price = float(latest["Close"])

        indicators = {
            "Close": close_price,
            "Open": float(latest.get("Open", 0.0)),
            "High": float(latest.get("High", 0.0)),
            "Low": float(latest.get("Low", 0.0)),
            "Volume": float(latest.get("Volume", 0.0)),
        }
        sub_scores = result.get("sub_scores")
        score_breakdown = {}
        final_score = 0.0
        if sub_scores:
            if hasattr(sub_scores, "to_dict"):
                score_breakdown = sub_scores.to_dict()
            elif hasattr(sub_scores, "composite_score"):
                score_breakdown = {
                    "accumulation": getattr(sub_scores, "accumulation_score", 0.0),
                    "compression": getattr(sub_scores, "compression_score", 0.0),
                    "rs": getattr(sub_scores, "rs_score", 0.0),
                    "resistance": getattr(sub_scores, "resistance_score", 0.0),
                    "composite": getattr(sub_scores, "composite_score", 0.0)
                }
            final_score = float(score_breakdown.get("composite", 0.0))

        # Gates
        gates = {}
        gates["ACCUMULATION_GATE"] = GateAuditResult(
            name="ACCUMULATION_GATE",
            passed=passed,
            status="PASS" if passed else "FAIL",
            actual=final_score,
            threshold=65.0,
            operator=">=",
            reason="Qualified Accumulation Setup" if passed else (reason or "Did not meet 12-step accumulation criteria")
        )

        # Frozen data snapshot
        ts_hash = get_dataframe_hash(df_daily)
        snapshot = FrozenDataSnapshot(
            symbol=symbol,
            row_count=len(df_daily),
            start_date=str(df_daily.iloc[0].get("Date", "N/A"))[:10],
            end_date=str(df_daily.iloc[-1].get("Date", "N/A"))[:10],
            sha256_hash=ts_hash
        )

        cfg_hash, norm_cfg = get_config_hash(effective_config or {})

        return ProductionDecisionRecord(
            symbol=symbol,
            scanner_name=self.scanner_name,
            evaluation_date=evaluation_date.split(" ")[0],
            evaluation_timestamp=datetime.now(IST).isoformat(),
            run_id=f"ACCUM_REPLAY_{symbol}_{evaluation_date.split(' ')[0]}",
            git_commit=get_git_commit(),
            scanner_file_hash=get_file_hash(os.path.join(_APP_DIR, "accumulation", "scanner.py")),
            config_hash=cfg_hash,
            effective_config=norm_cfg,
            market_regime="NEUTRAL",
            data_snapshot=snapshot,
            indicators=indicators,
            gate_results=gates,
            score_breakdown=score_breakdown,
            final_score=final_score,
            terminal_decision=decision,
            alert_generated=passed,
            rejection_reason=reason if not passed else None,
            primary_gate="ACCUMULATION_GATE" if not passed else None,
            replay_mode=mode.value
        )

    def _build_empty_record(self, symbol: str, eval_date: str, mode: ReplayMode, reason: str) -> ProductionDecisionRecord:
        return ProductionDecisionRecord(
            symbol=symbol,
            scanner_name=self.scanner_name,
            evaluation_date=eval_date.split(" ")[0],
            evaluation_timestamp=datetime.now(IST).isoformat(),
            run_id=f"ACCUM_REPLAY_{symbol}_{eval_date.split(' ')[0]}",
            git_commit=get_git_commit(),
            scanner_file_hash="N/A",
            config_hash="N/A",
            effective_config={},
            market_regime="NEUTRAL",
            data_snapshot=FrozenDataSnapshot(symbol=symbol, row_count=0, start_date="", end_date="", sha256_hash=""),
            indicators={},
            gate_results={},
            score_breakdown={},
            final_score=0.0,
            terminal_decision="REJECTED",
            alert_generated=False,
            rejection_reason=reason,
            primary_gate="DATA_SUFFICIENCY",
            replay_mode=mode.value
        )
