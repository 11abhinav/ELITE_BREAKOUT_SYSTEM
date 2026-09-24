# app/certification/adapters/eod_adapter.py
"""
EOD Breakout Scanner Certification Adapter.
Executes both Mode 1 (PRODUCTION_REPLAY) and Mode 2 (CLEAN_HISTORICAL_REPLAY).
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


class EODScannerAdapter(BaseScannerAdapter):
    """Adapter for EOD Breakout Scanner (eod_scanner.py)."""
    
    def __init__(self):
        super().__init__(scanner_type=ScannerType.EOD_BREAKOUT)

    def evaluate(
        self,
        symbol: str,
        evaluation_date: str,
        mode: ReplayMode = ReplayMode.CLEAN_HISTORICAL_REPLAY,
        prod_record: Optional[ProductionDecisionRecord] = None,
        custom_data: Optional[Dict[str, Any]] = None,
        effective_config: Optional[Dict[str, Any]] = None
    ) -> ProductionDecisionRecord:
        import eod_scanner
        from technical_indicators import hydrate_indicators

        # 1. Determine input DataFrame based on mode
        df = None
        if custom_data and "df" in custom_data:
            df = custom_data["df"]
        elif mode == ReplayMode.PRODUCTION_REPLAY and prod_record and prod_record.data_snapshot.raw_data_json:
            import json
            df = pd.read_json(prod_record.data_snapshot.raw_data_json)
        elif mode == ReplayMode.PRODUCTION_REPLAY and os.path.exists(os.path.join(_ROOT_DIR, "data", "history", "1d", f"{symbol}.parquet.corrupt_bak")):
            # If production evaluated before sanitization, the backup has the exact historical state
            df = pd.read_parquet(os.path.join(_ROOT_DIR, "data", "history", "1d", f"{symbol}.parquet.corrupt_bak"))
        else:
            # Clean historical replay uses current clean parquet
            parquet_path = os.path.join(_ROOT_DIR, "data", "history", "1d", f"{symbol}.parquet")
            if os.path.exists(parquet_path):
                df = pd.read_parquet(parquet_path)

        if df is None or df.empty:
            return ProductionDecisionRecord(
                symbol=symbol,
                scanner_name=self.scanner_type.value,
                evaluation_date=evaluation_date,
                evaluation_timestamp=datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
                run_id=f"replay_{int(datetime.now().timestamp())}",
                git_commit=get_git_commit(),
                scanner_file_hash=get_file_hash(os.path.join(_APP_DIR, "eod_scanner.py")),
                config_hash="EMPTY_CONFIG",
                effective_config={},
                market_regime="UNKNOWN",
                data_snapshot=FrozenDataSnapshot(symbol=symbol, row_count=0, start_date=evaluation_date, end_date=evaluation_date, sha256_hash="EMPTY"),
                indicators={},
                gate_results={},
                score_breakdown={},
                final_score=0.0,
                terminal_decision="REJECTED",
                alert_generated=False,
                rejection_reason="DATA_INSUFFICIENT",
                replay_mode=mode.value
            )

        # 2. Slice strictly up to evaluation date
        time_col = next((c for c in ["Datetime", "Date", "timestamp"] if c in df.columns), None)
        if time_col:
            eval_dt = datetime.strptime(evaluation_date.split(" ")[0], "%Y-%m-%d").date()
            dt_s = pd.to_datetime(df[time_col], utc=True)
            mask = dt_s.apply(lambda x: x.astimezone(IST).date() <= eval_dt)
            df_cut = df[mask].copy()
        else:
            df_cut = df.copy()

        df_cut = hydrate_indicators(df_cut, timeframe="1d")
        latest = df_cut.iloc[-1]

        # 3. Call production condition check
        cfg = effective_config or (prod_record.effective_config if prod_record else eod_scanner.EOD_ADVANCED_CONFIG)
        regime = prod_record.market_regime if prod_record else "STRONG_BEAR"
        deliv = prod_record.data_snapshot.delivery_pct if prod_record else None

        cond = eod_scanner._check_eod_conditions(
            ticker=df_cut,
            latest=latest,
            symbol=symbol,
            mode="ui",
            prior_high_source="raw",
            delivery_pct=deliv,
            regime_ctx={"market_regime": regime}
        )

        passed = cond.get("passed", False)
        reason = cond.get("reason")
        decision = "SELECTED" if passed else "REJECTED"

        # 4. Extract all technical indicators
        indicators = {}
        for col in ["Open", "High", "Low", "Close", "Volume", "RSI", "ATR", "ATR20", "ATR_PCT",
                    "BB_LOWER", "BB_MID", "BB_UPPER", "BB_WIDTH", "BB_WIDTH_PCTILE",
                    "HIGH_20D", "PRIOR_20D_HIGH", "HIGH_50D", "HIGH_100D", "HIGH_252D", "HIGH_52W",
                    "OBV", "OBV_20MA", "OBV_SLOPE", "OBV_TREND", "BASE_WIDTH", "VCP_TIGHTENING"]:
            if col in latest:
                val = latest[col]
                indicators[col] = float(val) if isinstance(val, (int, float, np.number)) and not pd.isna(val) else (str(val) if not pd.isna(val) else None)

        bar_high = float(latest.get("High", 0.0))
        bar_low = float(latest.get("Low", 0.0))
        c_range = cond.get("candle_range", bar_high - bar_low)
        atr20_val = cond.get("atr20", latest.get("ATR20", 0.0))
        if atr20_val is not None and not pd.isna(atr20_val):
            indicators["ATR20"] = float(atr20_val)
            if float(atr20_val) > 0:
                indicators["ATR_EXPANSION"] = float(c_range / float(atr20_val))
        if "volume_ratio" in cond:
            indicators["RVOL"] = float(cond["volume_ratio"])

        # 5. Extract all gates
        gates = {}
        atr_exp = indicators.get("ATR_EXPANSION")
        gates["NO_ATR_EXPANSION"] = GateAuditResult(
            name="NO_ATR_EXPANSION",
            passed=bool(atr_exp is not None and atr_exp >= 0.80),
            status="PASS" if (atr_exp is not None and atr_exp >= 0.80) else "FAIL",
            actual=atr_exp,
            threshold=0.80,
            operator=">=",
            reason="ATR expansion >= 0.80" if (atr_exp is not None and atr_exp >= 0.80) else "Candle range / ATR20 < 0.80"
        )
        rvol = indicators.get("RVOL")
        gates["LOW_VOLUME"] = GateAuditResult(
            name="LOW_VOLUME",
            passed=bool(rvol is not None and rvol >= 1.80),
            status="PASS" if (rvol is not None and rvol >= 1.80) else "FAIL",
            actual=rvol,
            threshold=1.80,
            operator=">=",
            reason="Volume ratio >= 1.80x" if (rvol is not None and rvol >= 1.80) else "Volume ratio < 1.80x"
        )
        close_p = indicators.get("Close")
        prior_h = indicators.get("PRIOR_20D_HIGH")
        gates["NO_STRUCTURAL_BREAKOUT"] = GateAuditResult(
            name="NO_STRUCTURAL_BREAKOUT",
            passed=bool(close_p and prior_h and close_p > prior_h),
            status="PASS" if (close_p and prior_h and close_p > prior_h) else "FAIL",
            actual=close_p,
            threshold=prior_h,
            operator=">",
            reason="Closed above prior 20D high" if (close_p and prior_h and close_p > prior_h) else "Close <= prior 20D high"
        )

        score = 0.0
        if passed:
            signals = eod_scanner.detect_breakouts(df_cut, timeframe="1d")
            score, _, _ = eod_scanner.calculate_score(
                category="EQUITY",
                breakout_count=len(signals),
                rsi=indicators.get("RSI", 50.0) or 50.0,
                volume_ratio=indicators.get("RVOL", 1.8) or 1.8,
                breakout_signals=signals,
                ticker=df_cut,
                latest=latest,
                symbol=symbol,
                timeframe="1d",
                atr_val=indicators.get("ATR20", 0.0),
                regime_ctx={"market_regime": regime}
            )

        cfg_hash, clean_cfg = get_config_hash(cfg)

        return ProductionDecisionRecord(
            symbol=symbol,
            scanner_name=self.scanner_type.value,
            evaluation_date=evaluation_date,
            evaluation_timestamp=datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
            run_id=f"replay_{int(datetime.now().timestamp())}",
            git_commit=get_git_commit(),
            scanner_file_hash=get_file_hash(os.path.join(_APP_DIR, "eod_scanner.py")),
            config_hash=cfg_hash,
            effective_config=clean_cfg,
            market_regime=regime,
            data_snapshot=FrozenDataSnapshot(
                symbol=symbol,
                row_count=len(df_cut),
                start_date=str(df_cut[time_col].iloc[0]) if time_col else evaluation_date,
                end_date=evaluation_date,
                sha256_hash=get_dataframe_hash(df_cut),
                delivery_pct=deliv,
                market_regime=regime
            ),
            indicators=indicators,
            gate_results=gates,
            score_breakdown={"score": score},
            final_score=float(score),
            terminal_decision=decision,
            alert_generated=(decision == "SELECTED"),
            rejection_reason=reason,
            primary_gate=reason,
            replay_mode=mode.value
        )
