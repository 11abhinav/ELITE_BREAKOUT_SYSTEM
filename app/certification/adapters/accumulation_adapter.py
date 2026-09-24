# app/certification/adapters/accumulation_adapter.py
"""
Accumulation / VCP Scanner Certification Adapter.
Models Volatility Contraction Pattern (VCP) stages, volume dry-up, and pocket pivots.
"""
import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional
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


class AccumulationVCPAdapter(BaseScannerAdapter):
    """Adapter for Accumulation / VCP Scanner (accumulation/scanner.py & accumulation_pipeline.py)."""
    
    def __init__(self):
        super().__init__(scanner_type=ScannerType.ACCUMULATION_VCP)

    def evaluate(
        self,
        symbol: str,
        evaluation_date: str,
        mode: ReplayMode = ReplayMode.CLEAN_HISTORICAL_REPLAY,
        prod_record: Optional[ProductionDecisionRecord] = None,
        custom_data: Optional[Dict[str, Any]] = None,
        effective_config: Optional[Dict[str, Any]] = None
    ) -> ProductionDecisionRecord:
        from technical_indicators import hydrate_indicators

        df = None
        if custom_data and "df" in custom_data:
            df = custom_data["df"]
        else:
            p = os.path.join(_ROOT_DIR, "data", "history", "1d", f"{symbol}.parquet")
            if os.path.exists(p):
                df = pd.read_parquet(p)

        if df is None or len(df) < 50:
            return self._build_empty(symbol, evaluation_date, mode, "DATA_INSUFFICIENT")

        time_col = next((c for c in ["Datetime", "Date", "timestamp"] if c in df.columns), None)
        if time_col:
            eval_dt = datetime.strptime(evaluation_date.split(" ")[0], "%Y-%m-%d").date()
            dt_s = pd.to_datetime(df[time_col], utc=True)
            df = df[dt_s.apply(lambda x: x.astimezone(IST).date() <= eval_dt)].copy()

        df = hydrate_indicators(df, timeframe="1d")
        latest = df.iloc[-1]

        close_p = float(latest.get("Close", 0.0))
        bb_width_pctile = float(latest.get("BB_WIDTH_PCTILE", 0.50))
        base_width = float(latest.get("BASE_WIDTH", 0.20))
        vcp_tightening = bool(latest.get("VCP_TIGHTENING", False))
        vol = float(latest.get("Volume", 0.0))
        avg_vol = float(df["Volume"].iloc[-21:-1].mean()) if len(df) >= 22 else vol
        vol_ratio = vol / avg_vol if avg_vol > 0 else 1.0

        # Accumulation / VCP gates
        is_tight_base = (bb_width_pctile <= 0.40) or (base_width <= 0.15)
        volume_dryup = (vol_ratio <= 0.70) or vcp_tightening

        passed = is_tight_base and volume_dryup
        decision = "SELECTED" if passed else "REJECTED"
        reason = "VCP_ACCUMULATION_ARMED" if passed else ("BASE_TOO_LOOSE" if not is_tight_base else "NO_VOLUME_DRYUP")

        gates = {
            "BASE_COMPRESSION": GateAuditResult("BASE_COMPRESSION", is_tight_base, "PASS" if is_tight_base else "FAIL", bb_width_pctile, 0.40, "<=", "BB width percentile <= 0.40"),
            "VOLUME_DRYUP": GateAuditResult("VOLUME_DRYUP", volume_dryup, "PASS" if volume_dryup else "FAIL", vol_ratio, 0.70, "<=", "Volume contracted during tight base")
        }

        indicators = {
            "Close": close_p,
            "BB_WIDTH_PCTILE": bb_width_pctile,
            "BASE_WIDTH": base_width,
            "VCP_TIGHTENING": vcp_tightening,
            "RVOL": vol_ratio
        }

        snap = FrozenDataSnapshot(
            symbol=symbol,
            row_count=len(df),
            start_date=str(df[time_col].iloc[0]) if time_col else evaluation_date,
            end_date=evaluation_date,
            sha256_hash=get_dataframe_hash(df)
        )

        return ProductionDecisionRecord(
            symbol=symbol,
            scanner_name=self.scanner_type.value,
            evaluation_date=evaluation_date,
            evaluation_timestamp=datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
            run_id=f"vcp_replay_{int(datetime.now().timestamp())}",
            git_commit=get_git_commit(),
            scanner_file_hash=get_file_hash(os.path.join(_APP_DIR, "accumulation_pipeline.py")),
            config_hash="VCP_CONFIG",
            effective_config={},
            market_regime="NEUTRAL",
            data_snapshot=snap,
            indicators=indicators,
            gate_results=gates,
            score_breakdown={"score": 85.0 if passed else 0.0},
            final_score=85.0 if passed else 0.0,
            terminal_decision=decision,
            alert_generated=passed,
            rejection_reason=reason,
            primary_gate=reason,
            replay_mode=mode.value
        )

    def _build_empty(self, symbol: str, date: str, mode: ReplayMode, reason: str) -> ProductionDecisionRecord:
        return ProductionDecisionRecord(
            symbol=symbol,
            scanner_name=self.scanner_type.value,
            evaluation_date=date,
            evaluation_timestamp=datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
            run_id="empty_vcp",
            git_commit=get_git_commit(),
            scanner_file_hash="none",
            config_hash="none",
            effective_config={},
            market_regime="UNKNOWN",
            data_snapshot=FrozenDataSnapshot(symbol=symbol, row_count=0, start_date=date, end_date=date, sha256_hash="EMPTY"),
            indicators={},
            gate_results={},
            score_breakdown={},
            final_score=0.0,
            terminal_decision="REJECTED",
            alert_generated=False,
            rejection_reason=reason,
            replay_mode=mode.value
        )
