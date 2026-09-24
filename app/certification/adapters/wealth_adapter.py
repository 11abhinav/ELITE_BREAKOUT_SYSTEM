# app/certification/adapters/wealth_adapter.py
"""
Wealth Compounder Scanner Certification Adapter (app/wealth_engine.py).
Evaluates 4 buckets (Core Compounder, Growth Multiplier, Quality-On-Sale, Opportunistic),
CMP > SMA200 trend gate, PEG ceiling, ROCE/ROE, and targets without side effects.
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


class WealthScannerAdapter(BaseScannerAdapter):
    """Adapter for Wealth Compounder Engine (app/wealth_engine.py)."""

    def __init__(self, scanner_type: ScannerType = ScannerType.WEALTH):
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
        import wealth_engine

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
            return self._build_empty_record(symbol, evaluation_date, mode, "INSUFFICIENT_BARS_FOR_WEALTH")

        # 3. Invoke Wealth Engine evaluation core
        result = wealth_engine.evaluate_wealth_symbol(symbol=symbol, df=df_daily, fund_data=fund_data)

        qualified = result.get("qualified", False)
        status_str = result.get("status", "NO")
        score = float(result.get("score", 0.0))
        reasons = result.get("reasons", [])
        buckets = result.get("buckets", [])
        decision = "SELECTED" if qualified else "REJECTED"

        latest = df_daily.iloc[-1]
        close_price = float(latest["Close"])
        high_52w = float(df_daily["High"].iloc[-252:].max()) if len(df_daily) >= 252 else float(df_daily["High"].max())
        drop_52w = ((high_52w - close_price) / high_52w) * 100.0 if high_52w > 0 else 0.0

        # Technical indicators
        indicators = {
            "Close": close_price,
            "High52W": high_52w,
            "Drop52WPct": drop_52w,
            "Volume": float(latest.get("Volume", 0.0)),
        }
        for col in ["SMA200", "SMA50", "ATR", "RSI"]:
            if col in latest and not pd.isna(latest[col]):
                indicators[col] = float(latest[col])
            elif col == "SMA200" and len(df_daily) >= 200:
                indicators["SMA200"] = float(df_daily["Close"].tail(200).mean())

        # Gates
        gates = {}
        sma200 = indicators.get("SMA200")
        trend_ok = bool(sma200 is not None and sma200 > 0 and close_price > sma200)
        gates["TREND_SMA200"] = GateAuditResult(
            name="TREND_SMA200",
            passed=trend_ok,
            status="PASS" if trend_ok else "FAIL",
            actual=close_price,
            threshold=sma200,
            operator=">",
            reason="Above SMA200" if trend_ok else "Below SMA200"
        )

        gates["QUALIFIED_BUCKETS"] = GateAuditResult(
            name="QUALIFIED_BUCKETS",
            passed=qualified,
            status="PASS" if qualified else "FAIL",
            actual=len(buckets),
            threshold=1,
            operator=">=",
            reason=f"Buckets: {', '.join(buckets)}" if buckets else "No buckets qualified"
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
            run_id=f"WEALTH_REPLAY_{symbol}_{evaluation_date.split(' ')[0]}",
            git_commit=get_git_commit(),
            scanner_file_hash=get_file_hash(wealth_engine.__file__),
            config_hash=cfg_hash,
            effective_config=norm_cfg,
            market_regime="NEUTRAL",
            data_snapshot=snapshot,
            indicators=indicators,
            gate_results=gates,
            score_breakdown={"gradient_score": score, "bucket_count": len(buckets)},
            final_score=score,
            terminal_decision=decision,
            alert_generated=qualified,
            rejection_reason=reasons[0] if (not qualified and reasons) else None,
            primary_gate="QUALIFIED_BUCKETS" if not qualified else None,
            replay_mode=mode.value
        )

    def _build_empty_record(self, symbol: str, eval_date: str, mode: ReplayMode, reason: str) -> ProductionDecisionRecord:
        return ProductionDecisionRecord(
            symbol=symbol,
            scanner_name=self.scanner_name,
            evaluation_date=eval_date.split(" ")[0],
            evaluation_timestamp=datetime.now(IST).isoformat(),
            run_id=f"WEALTH_REPLAY_{symbol}_{eval_date.split(' ')[0]}",
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
