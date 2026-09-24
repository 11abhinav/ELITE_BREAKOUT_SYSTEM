# app/certification/adapters/daily_builder_adapter.py
"""
Daily Builder / EOD Breakout V2 Certification Adapter (app/eod_v2_engine.py).
Evaluates zero-lookahead prior 20d high, structure valid check,
anti-false-breakout guardrails, and deterministic lifecycle state transitions.
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


class DailyBuilderAdapter(BaseScannerAdapter):
    """Adapter for Daily Builder / EOD Breakout V2 (app/eod_v2_engine.py)."""

    def __init__(self, scanner_type: ScannerType = ScannerType.DAILY_BUILDER):
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
        import eod_v2_engine

        # 1. Load historical daily price data
        df_daily = None
        if custom_data:
            df_daily = custom_data.get("df")

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

        if len(df_daily) < 22:
            return self._build_empty_record(symbol, evaluation_date, mode, "INSUFFICIENT_BARS_FOR_DAILY_BUILDER")

        # 3. Invoke EOD V2 Engine evaluation
        result = eod_v2_engine.evaluate_eod_v2_symbol(symbol=symbol, df=df_daily)

        state = result.get("state", "REJECTED")
        is_candidate = result.get("is_candidate", False)
        confirmed = (state in ("CONFIRMED", "CANDIDATE", "IMMEDIATE_TRIGGER_ZONE"))
        rejection_reason = result.get("rejection_reason")
        decision = "SELECTED" if confirmed else "REJECTED"

        latest = df_daily.iloc[-1]
        close_price = float(latest["Close"])
        prior_high = float(result.get("prior_20d_high", 0.0) or 0.0)
        vol_ratio = float(result.get("volume_ratio", 1.0) or 1.0)

        indicators = {
            "Close": close_price,
            "Open": float(latest.get("Open", 0.0)),
            "High": float(latest.get("High", 0.0)),
            "Low": float(latest.get("Low", 0.0)),
            "Volume": float(latest.get("Volume", 0.0)),
            "Prior20DHigh": prior_high,
            "VolumeRatio": vol_ratio,
            "BreakoutDistPct": float(result.get("breakout_dist_pct", 0.0) or 0.0),
        }

        # Gates
        gates = {}
        struct_valid = bool(result.get("structure_valid", False))
        gates["STRUCTURE_VALID"] = GateAuditResult(
            name="STRUCTURE_VALID",
            passed=struct_valid,
            status="PASS" if struct_valid else "FAIL",
            actual=prior_high,
            threshold=0.0,
            operator=">",
            reason="Structure valid" if struct_valid else "Structure invalid / lack of base"
        )

        gates["STAGE2_BREAKOUT"] = GateAuditResult(
            name="STAGE2_BREAKOUT",
            passed=confirmed,
            status="PASS" if confirmed else "FAIL",
            actual=close_price,
            threshold=prior_high,
            operator=">=",
            reason=f"State: {state}" if confirmed else (rejection_reason or f"State: {state}")
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
            run_id=f"DAILY_BUILDER_REPLAY_{symbol}_{evaluation_date.split(' ')[0]}",
            git_commit=get_git_commit(),
            scanner_file_hash=get_file_hash(eod_v2_engine.__file__),
            config_hash=cfg_hash,
            effective_config=norm_cfg,
            market_regime="NEUTRAL",
            data_snapshot=snapshot,
            indicators=indicators,
            gate_results=gates,
            score_breakdown={"state": state, "vol_ratio": vol_ratio},
            final_score=float(result.get("quality_score", 0.0) or 0.0),
            terminal_decision=decision,
            alert_generated=confirmed,
            rejection_reason=rejection_reason if not confirmed else None,
            primary_gate="STAGE2_BREAKOUT" if not confirmed else None,
            replay_mode=mode.value
        )

    def _build_empty_record(self, symbol: str, eval_date: str, mode: ReplayMode, reason: str) -> ProductionDecisionRecord:
        return ProductionDecisionRecord(
            symbol=symbol,
            scanner_name=self.scanner_name,
            evaluation_date=eval_date.split(" ")[0],
            evaluation_timestamp=datetime.now(IST).isoformat(),
            run_id=f"DAILY_BUILDER_REPLAY_{symbol}_{eval_date.split(' ')[0]}",
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
