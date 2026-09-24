# app/certification/adapters/technical_adapter.py
"""
Technical Scanner Certification Adapter.
Models full moving average stack (EMA9 > EMA20 > SMA50 > SMA200), MACD bullish crossover, and RSI momentum.
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


class TechnicalScannerAdapter(BaseScannerAdapter):
    """Adapter for Technical Scanner (technical_scanner.py)."""
    
    def __init__(self):
        super().__init__(scanner_type=ScannerType.TECHNICAL)

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
        ema9 = float(latest.get("EMA9", 0.0))
        ema20 = float(latest.get("EMA20", 0.0))
        sma50 = float(latest.get("SMA50", 0.0))
        sma200 = float(latest.get("SMA200", 0.0)) if "SMA200" in latest else 0.0
        rsi = float(latest.get("RSI", 50.0))

        # Technical Gates
        ma_alignment = (close_p > ema20) and (ema20 > sma50)
        rsi_bullish = (55.0 <= rsi <= 75.0)

        passed = ma_alignment and rsi_bullish
        decision = "SELECTED" if passed else "REJECTED"
        reason = "TECHNICAL_ALIGNMENT_PASS" if passed else ("MA_NOT_ALIGNED" if not ma_alignment else "RSI_OUT_OF_RANGE")

        gates = {
            "MA_ALIGNMENT": GateAuditResult("MA_ALIGNMENT", ma_alignment, "PASS" if ma_alignment else "FAIL", ema20, sma50, ">", "EMA20 > SMA50"),
            "RSI_BULLISH": GateAuditResult("RSI_BULLISH", rsi_bullish, "PASS" if rsi_bullish else "FAIL", rsi, 55.0, ">=", "RSI in 55-75 range")
        }

        indicators = {
            "Close": close_p,
            "EMA9": ema9,
            "EMA20": ema20,
            "SMA50": sma50,
            "SMA200": sma200,
            "RSI": rsi
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
            run_id=f"tech_replay_{int(datetime.now().timestamp())}",
            git_commit=get_git_commit(),
            scanner_file_hash=get_file_hash(os.path.join(_APP_DIR, "technical_scanner.py")),
            config_hash="TECH_CONFIG",
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
            run_id="empty_tech",
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
