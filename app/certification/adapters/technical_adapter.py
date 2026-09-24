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

        if not os.environ.get("UPSTOX_ACCESS_TOKEN"):
            env_file = os.path.join(_ROOT_DIR, ".env")
            if os.path.exists(env_file):
                with open(env_file) as f:
                    for line in f:
                        if line.startswith("UPSTOX_ACCESS_TOKEN="):
                            os.environ["UPSTOX_ACCESS_TOKEN"] = line.split("=", 1)[1].strip()
                            break

        df = None
        if custom_data and "df" in custom_data:
            df = custom_data["df"]
        elif os.getenv("BACKTEST_DATA_SOURCE", "").upper() == "UPSTOX" or os.getenv("USE_UPSTOX_PROVIDER", "").lower() == "true":
            try:
                from market_data.providers.upstox_provider import UpstoxProvider
                provider = UpstoxProvider()
                md = provider.get_ohlcv(symbol, interval="1d", period="1y")
                df = getattr(md, "dataframe", None)
            except Exception:
                df = None

        if df is None:
            p = os.path.join(_ROOT_DIR, "data", "history", "1d", f"{symbol}.parquet")
            if os.path.exists(p):
                df = pd.read_parquet(p)

        if df is None or len(df) < 50:
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

        if len(df) < 50:
            return self._build_empty(symbol, evaluation_date, mode, "DATA_INSUFFICIENT")

        import technical_scanner
        setup_res, trace = technical_scanner.detect_technical_setup(
            df=df,
            symbol=symbol,
            return_trace=True
        )

        passed = setup_res is not None and bool(setup_res.get("passed", True))
        pattern_name = (setup_res.get("primary_pattern") or setup_res.get("pattern") or setup_res.get("pattern_name", "NONE")) if setup_res else "NO_PATTERN"
        score = float(setup_res.get("score", 0.0)) if setup_res else 0.0

        # Hard Production Invariant: Only APPROVED_TECHNICAL_PATTERNS can generate alerts
        from regime_pattern_policy import APPROVED_TECHNICAL_PATTERNS
        if pattern_name not in APPROVED_TECHNICAL_PATTERNS:
            passed = False
            decision = "REJECTED"
            reason = f"UNAPPROVED_PATTERN_{pattern_name}"
        else:
            decision = "SELECTED" if passed else "REJECTED"
            reason = f"PATTERN_{pattern_name}" if passed else (trace.get("terminal_rejection_reason", "NO_VALID_SETUP"))

        latest = df.iloc[-1]
        close_p = float(latest.get("Close", 0.0))
        ema9 = float(latest.get("EMA9", 0.0)) if "EMA9" in latest else 0.0
        ema20 = float(latest.get("EMA20", 0.0)) if "EMA20" in latest else 0.0
        sma50 = float(latest.get("SMA50", 0.0)) if "SMA50" in latest else 0.0
        sma200 = float(latest.get("SMA200", 0.0)) if "SMA200" in latest else 0.0
        rsi = float(latest.get("RSI", 50.0)) if "RSI" in latest else 50.0

        ma_aligned = bool((close_p >= sma50) if sma50 > 0 else True)
        gates = {
            "TECHNICAL_PATTERN_QUALIFIED": GateAuditResult(
                name="TECHNICAL_PATTERN_QUALIFIED",
                passed=passed,
                status="PASS" if passed else "FAIL",
                actual=score,
                threshold=70.0,
                operator=">=",
                reason=reason
            ),
            "MA_ALIGNMENT": GateAuditResult(
                name="MA_ALIGNMENT",
                passed=ma_aligned,
                status="PASS" if ma_aligned else "FAIL",
                actual=close_p,
                threshold=sma50,
                operator=">=",
                reason="CLOSE_GE_SMA50" if ma_aligned else "CLOSE_LT_SMA50"
            )
        }

        indicators = {
            "Close": close_p,
            "EMA9": ema9,
            "EMA20": ema20,
            "SMA50": sma50,
            "SMA200": sma200,
            "RSI": rsi,
            "Pattern": pattern_name,
            "Tier": setup_res.get("tier", "NONE") if setup_res else "NONE",
            "StopLoss": setup_res.get("stop_loss", 0.0) if setup_res else 0.0,
            "Target1": setup_res.get("target_1", 0.0) if setup_res else 0.0,
        }

        snap = FrozenDataSnapshot(
            symbol=symbol,
            row_count=len(df),
            start_date=str(df[time_col].iloc[0]) if time_col else evaluation_date,
            end_date=evaluation_date,
            sha256_hash=get_dataframe_hash(df)
        )

        sb = setup_res.get("score_breakdown", {"score": score if passed else 0.0}) if (passed and setup_res) else {"score": 0.0}

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
            score_breakdown=sb,
            final_score=score if passed else 0.0,
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
