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

        # Slice to evaluation date
        time_col = next((c for c in ["Datetime", "Date", "timestamp"] if c in df.columns), None)
        if time_col:
            eval_dt = datetime.strptime(evaluation_date.split(" ")[0], "%Y-%m-%d").date()
            dt_s = pd.to_datetime(df[time_col], utc=True)
            df = df[dt_s.apply(lambda x: x.astimezone(IST).date() <= eval_dt)].copy()

        df = hydrate_indicators(df, timeframe="1d")
        latest = df.iloc[-1]
        prev = df.iloc[-2]

        close_p = float(latest.get("Close", 0.0))
        open_p = float(latest.get("Open", 0.0))
        high_p = float(latest.get("High", 0.0))
        low_p = float(latest.get("Low", 0.0))
        rsi_val = float(latest.get("RSI", 50.0))
        vol = float(latest.get("Volume", 0.0))
        avg_vol = float(df["Volume"].iloc[-21:-1].mean()) if len(df) >= 22 else vol
        vol_ratio = vol / avg_vol if avg_vol > 0 else 1.0

        # Reversal conditions: Hammer / Bullish Engulfing after selloff + RSI oversold recovery
        is_hammer = (close_p > open_p) and ((open_p - low_p) >= 2.0 * abs(close_p - open_p)) and ((high_p - close_p) <= 0.2 * (high_p - low_p))
        is_engulfing = (close_p > open_p) and (float(prev.get("Close", 0.0)) < float(prev.get("Open", 0.0))) and (close_p >= float(prev.get("High", 0.0))) and (open_p <= float(prev.get("Low", 0.0)))
        has_candle_pattern = is_hammer or is_engulfing

        rsi_oversold_recovery = (rsi_val >= 30.0 and float(prev.get("RSI", rsi_val)) < 30.0) or (rsi_val <= 38.0)
        volume_exhaustion = (vol_ratio >= 1.50)

        passed = has_candle_pattern and rsi_oversold_recovery and volume_exhaustion
        decision = "SELECTED" if passed else "REJECTED"
        reason = "VALID_REVERSAL" if passed else ("NO_REVERSAL_CANDLE" if not has_candle_pattern else ("RSI_NOT_OVERSOLD" if not rsi_oversold_recovery else "LOW_EXHAUSTION_VOLUME"))

        gates = {
            "REVERSAL_CANDLE_PATTERN": GateAuditResult(
                name="REVERSAL_CANDLE_PATTERN",
                passed=has_candle_pattern,
                status="PASS" if has_candle_pattern else "FAIL",
                actual=1.0 if has_candle_pattern else 0.0,
                threshold=1.0,
                operator="==",
                reason="Bullish hammer or engulfing candle" if has_candle_pattern else "No reversal candle pattern"
            ),
            "RSI_OVERSOLD_RECOVERY": GateAuditResult(
                name="RSI_OVERSOLD_RECOVERY",
                passed=rsi_oversold_recovery,
                status="PASS" if rsi_oversold_recovery else "FAIL",
                actual=rsi_val,
                threshold=38.0,
                operator="<=",
                reason="RSI in oversold recovery zone" if rsi_oversold_recovery else "RSI not in oversold zone"
            ),
            "EXHAUSTION_VOLUME": GateAuditResult(
                name="EXHAUSTION_VOLUME",
                passed=volume_exhaustion,
                status="PASS" if volume_exhaustion else "FAIL",
                actual=vol_ratio,
                threshold=1.50,
                operator=">=",
                reason="Exhaustion volume >= 1.50x" if volume_exhaustion else "Volume ratio < 1.50x"
            )
        }

        indicators = {
            "Close": close_p,
            "RSI": rsi_val,
            "RVOL": vol_ratio,
            "IS_HAMMER": is_hammer,
            "IS_ENGULFING": is_engulfing
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
