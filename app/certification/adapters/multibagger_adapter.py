# app/certification/adapters/multibagger_adapter.py
"""
Multibagger V5 Scanner Certification Adapter (app/multibagger.py).
Evaluates full V5 composite scoring, quality/valuation/trend gates,
Piotroski & promoter pledge checks, conviction tier classification, and target calculations.
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


class MultibaggerScannerAdapter(BaseScannerAdapter):
    """Adapter for Multibagger V5 Engine (app/multibagger.py)."""

    def __init__(self, scanner_type: ScannerType = ScannerType.MULTIBAGGER):
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
        import multibagger

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
            eval_dt = datetime.strptime(evaluation_date.split(" ")[0], "%Y-%m-%d").date()
            dt_s = pd.to_datetime(df_daily[time_col], utc=True).dt.tz_convert(IST).dt.date
            df_daily = df_daily[dt_s <= eval_dt].copy()

        if len(df_daily) < 15:
            return self._build_empty_record(symbol, evaluation_date, mode, "INSUFFICIENT_BARS_FOR_MULTIBAGGER")

        # 3. Invoke Multibagger evaluation core
        result = multibagger.evaluate_multibagger_symbol(symbol=symbol, df=df_daily, fund_data=fund_data)

        qualified = result.get("qualified", False)
        status_str = result.get("status", "NO")
        score = float(result.get("score", 0.0))
        reasons = result.get("reasons", [])
        tier = result.get("tier", "NONE")
        decision = "SELECTED" if qualified else "REJECTED"

        latest = df_daily.iloc[-1]
        close_price = float(latest["Close"])

        indicators = {
            "Close": close_price,
            "Open": float(latest.get("Open", 0.0)),
            "High": float(latest.get("High", 0.0)),
            "Low": float(latest.get("Low", 0.0)),
            "Volume": float(latest.get("Volume", 0.0)),
            "Piotroski_Score": float(result.get("f_score", 0.0) or 0.0),
            "Pledge_Ratio": float(result.get("pledge_ratio", 0.0) or 0.0),
        }
        for col in ["SMA50", "SMA200", "ATR", "RSI"]:
            if col in latest and not pd.isna(latest[col]):
                indicators[col] = float(latest[col])

        # Gates
        gates = {}
        gates["CONVICTION_QUALIFIED"] = GateAuditResult(
            name="CONVICTION_QUALIFIED",
            passed=qualified,
            status="PASS" if qualified else "FAIL",
            actual=score,
            threshold=50.0,
            operator=">=",
            reason=f"Tier: {tier}, Score: {score:.1f}" if qualified else (reasons[0] if reasons else "Score below threshold")
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
            run_id=f"MULTIBAGGER_REPLAY_{symbol}_{evaluation_date.split(' ')[0]}",
            git_commit=get_git_commit(),
            scanner_file_hash=get_file_hash(multibagger.__file__),
            config_hash=cfg_hash,
            effective_config=norm_cfg,
            market_regime="NEUTRAL",
            data_snapshot=snapshot,
            indicators=indicators,
            gate_results=gates,
            score_breakdown={"v5_score": score, "tier": tier},
            final_score=score,
            terminal_decision=decision,
            alert_generated=qualified,
            rejection_reason=reasons[0] if (not qualified and reasons) else None,
            primary_gate="CONVICTION_QUALIFIED" if not qualified else None,
            replay_mode=mode.value
        )

    def _build_empty_record(self, symbol: str, eval_date: str, mode: ReplayMode, reason: str) -> ProductionDecisionRecord:
        return ProductionDecisionRecord(
            symbol=symbol,
            scanner_name=self.scanner_name,
            evaluation_date=eval_date.split(" ")[0],
            evaluation_timestamp=datetime.now(IST).isoformat(),
            run_id=f"MULTIBAGGER_REPLAY_{symbol}_{eval_date.split(' ')[0]}",
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
