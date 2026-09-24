# app/certification/adapters/pullback_adapter.py
"""
Pullback Scanner Certification Adapter.
Models primary trend alignment, pullback depth to EMA20/SMA50, support hold, and bounce confirmation.
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


class PullbackScannerAdapter(BaseScannerAdapter):
    """Adapter for Pullback Scanner (pullback_pipeline.py & pullback_engine.py)."""
    
    def __init__(self):
        super().__init__(scanner_type=ScannerType.PULLBACK)

    def evaluate(
        self,
        symbol: str,
        evaluation_date: str,
        mode: ReplayMode = ReplayMode.CLEAN_HISTORICAL_REPLAY,
        prod_record: Optional[ProductionDecisionRecord] = None,
        custom_data: Optional[Dict[str, Any]] = None,
        effective_config: Optional[Dict[str, Any]] = None
    ) -> ProductionDecisionRecord:
        import pullback_engine
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
        open_p = float(latest.get("Open", 0.0))
        low_p = float(latest.get("Low", 0.0))
        ema20 = float(latest.get("EMA20", 0.0))
        sma50 = float(latest.get("SMA50", 0.0))
        atr20 = float(latest.get("ATR20", close_p * 0.025))

        # Pullback gates
        primary_uptrend = (close_p > sma50) and (ema20 > sma50)
        # Pullback into value zone: within 1.0 ATR of EMA20
        dist_ema20_atr = abs(close_p - ema20) / atr20 if atr20 > 0 else 0.0
        in_value_zone = (dist_ema20_atr <= 1.0)
        # Support holds: low did not slice more than 0.5 ATR below EMA20
        support_held = (low_p >= ema20 - 0.5 * atr20)
        # Bounce confirmation: close >= open
        bounce_confirmed = (close_p >= open_p)

        passed = primary_uptrend and in_value_zone and support_held and bounce_confirmed
        decision = "SELECTED" if passed else "REJECTED"
        reason = "VALID_PULLBACK" if passed else ("NOT_IN_UPTREND" if not primary_uptrend else ("OUTSIDE_VALUE_ZONE" if not in_value_zone else ("SUPPORT_BROKEN" if not support_held else "BEARISH_CANDLE")))

        gates = {
            "PRIMARY_UPTREND": GateAuditResult("PRIMARY_UPTREND", primary_uptrend, "PASS" if primary_uptrend else "FAIL", close_p, sma50, ">", "Close & EMA20 > SMA50"),
            "VALUE_ZONE_DEPTH": GateAuditResult("VALUE_ZONE_DEPTH", in_value_zone, "PASS" if in_value_zone else "FAIL", dist_ema20_atr, 1.0, "<=", "Within 1.0 ATR of EMA20"),
            "SUPPORT_HOLD": GateAuditResult("SUPPORT_HOLD", support_held, "PASS" if support_held else "FAIL", low_p, ema20 - 0.5 * atr20, ">=", "Low defended EMA20 buffer"),
            "BOUNCE_CONFIRMATION": GateAuditResult("BOUNCE_CONFIRMATION", bounce_confirmed, "PASS" if bounce_confirmed else "FAIL", close_p, open_p, ">=", "Bullish bounce bar")
        }

        indicators = {
            "Close": close_p,
            "EMA20": ema20,
            "SMA50": sma50,
            "ATR20": atr20,
            "DIST_EMA20_ATR": round(dist_ema20_atr, 2)
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
            run_id=f"pb_replay_{int(datetime.now().timestamp())}",
            git_commit=get_git_commit(),
            scanner_file_hash=get_file_hash(os.path.join(_APP_DIR, "pullback_pipeline.py")),
            config_hash="PULLBACK_CONFIG",
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
            run_id="empty_pb",
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
