# app/certification/adapters/reversal_adapter.py
"""
Reversal Scanner Certification Adapter.
Models exhaustion volume, hammer/engulfing candles, RSI oversold/divergence gates.
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


class ReversalScannerAdapter(BaseScannerAdapter):
    """Adapter for Reversal Scanner (reversal_scanner.py & reversal_engine.py)."""
    
    def __init__(self):
        super().__init__(scanner_type=ScannerType.REVERSAL)

    def evaluate(
        self,
        symbol: str,
        evaluation_date: str,
        mode: ReplayMode = ReplayMode.CLEAN_HISTORICAL_REPLAY,
        prod_record: Optional[ProductionDecisionRecord] = None,
        custom_data: Optional[Dict[str, Any]] = None,
        effective_config: Optional[Dict[str, Any]] = None
    ) -> ProductionDecisionRecord:
        import reversal_engine
        from technical_indicators import hydrate_indicators

        df = None
        if custom_data and "df" in custom_data:
            df = custom_data["df"]
        else:
            p = os.path.join(_ROOT_DIR, "data", "history", "1d", f"{symbol}.parquet")
            if os.path.exists(p):
                df = pd.read_parquet(p)

        if df is None or len(df) < 20:
            return self._build_empty(symbol, evaluation_date, mode, "DATA_INSUFFICIENT")

        # Slice strictly to evaluation date (vectorized)
        eval_dt_str = evaluation_date.split(" ")[0]
        time_col = next((c for c in ["Datetime", "Date", "timestamp"] if c in df.columns), None)
        if time_col:
            dt_str_series = df[time_col].astype(str).str[:10]
            df = df[dt_str_series <= eval_dt_str].copy()
        elif isinstance(df.index, pd.DatetimeIndex):
            dt_str_series = df.index.astype(str).str[:10]
            df = df[dt_str_series <= eval_dt_str].copy()

        if len(df) < 20:
            return self._build_empty(symbol, evaluation_date, mode, "DATA_INSUFFICIENT")

        regime = prod_record.market_regime if prod_record else "NEUTRAL"
        import reversal_scanner
        eval_res = reversal_scanner.evaluate_reversal_symbol(
            symbol=symbol,
            ticker=df,
            fund_data=custom_data.get("fund_data") if custom_data else None,
            regime_ctx={"current_regime": regime, "trend": regime}
        )

        passed = eval_res.get("qualified", False)
        decision = "SELECTED" if passed else "REJECTED"
        reasons_list = eval_res.get("reasons", [])
        reason = reasons_list[0] if reasons_list else ("VALID_REVERSAL" if passed else "REJECTED")
        score = float(eval_res.get("score", 0.0))

        latest = df.iloc[-1]
        close_p = float(latest.get("Close", 0.0))
        rsi_val = float(latest.get("RSI", 50.0)) if "RSI" in latest else 50.0
        vol = float(latest.get("Volume", 0.0))
        avg_vol = float(df["Volume"].iloc[-21:-1].mean()) if len(df) >= 22 else vol
        vol_ratio = vol / avg_vol if avg_vol > 0 else 1.0

        gates = {
            "REVERSAL_QUALIFIED": GateAuditResult(
                name="REVERSAL_QUALIFIED",
                passed=passed,
                status="PASS" if passed else "FAIL",
                actual=score,
                threshold=75.0,
                operator=">=",
                reason=reason
            )
        }

        indicators = {
            "Close": close_p,
            "RSI": rsi_val,
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
            run_id=f"rev_replay_{int(datetime.now().timestamp())}",
            git_commit=get_git_commit(),
            scanner_file_hash=get_file_hash(os.path.join(_APP_DIR, "reversal_scanner.py")),
            config_hash="REVERSAL_CONFIG",
            effective_config={},
            market_regime="NEUTRAL",
            data_snapshot=snap,
            indicators=indicators,
            gate_results=gates,
            score_breakdown={"score": 80.0 if passed else 0.0},
            final_score=80.0 if passed else 0.0,
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
            run_id="empty_rev",
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
