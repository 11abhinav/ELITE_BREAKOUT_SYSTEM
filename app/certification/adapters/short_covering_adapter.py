# app/certification/adapters/short_covering_adapter.py
"""
Short Covering Scanner Certification Adapter (EOD and 5M Intraday).
Captures F&O universe, futures contract mapping, OI health, data sufficiency,
and reproduces DATA_INSUFFICIENT / BLOCKED states.
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
    ShortCoveringDataHealth,
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


class ShortCoveringScannerAdapter(BaseScannerAdapter):
    """Adapter for Short Covering Scanners (short_covering_scanner.py & short_covering_5m.py)."""
    
    def __init__(self, scanner_type: ScannerType = ScannerType.SHORT_COVERING_EOD):
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
        # 1. Determine F&O Universe status
        try:
            import config
            fo_universe = getattr(config, "FO_UNIVERSE", [])
        except Exception:
            fo_universe = []
        in_fo = (symbol in fo_universe) if fo_universe else True

        if not in_fo:
            health = ShortCoveringDataHealth(
                symbol=symbol,
                in_fo_universe=False,
                health_status="BLOCKED",
                oi_available=False
            )
            return self._build_record(
                symbol=symbol,
                eval_date=evaluation_date,
                decision="REJECTED",
                reason="NOT_IN_FO_UNIVERSE",
                score=0.0,
                health=health,
                mode=mode,
                indicators={"in_fo": False}
            )

        # 2. Check 5M or EOD data availability
        df_5m = None
        if custom_data and "df_5m" in custom_data:
            df_5m = custom_data["df_5m"]
        else:
            p5 = os.path.join(_ROOT_DIR, "data", "history", "5m", f"{symbol}.parquet")
            if os.path.exists(p5):
                df_5m = pd.read_parquet(p5)

        # If data is present, slice strictly up to evaluation_date
        if df_5m is not None and not df_5m.empty:
            time_col = next((c for c in ["timestamp", "Datetime", "Date", "date"] if c in df_5m.columns), None)
            if time_col:
                eval_dt_str = evaluation_date.split(" ")[0]
                dt_str_series = df_5m[time_col].astype(str).str[:10]
                df_5m = df_5m[dt_str_series <= eval_dt_str].copy()
            elif isinstance(df_5m.index, pd.DatetimeIndex):
                eval_dt_str = evaluation_date.split(" ")[0]
                dt_str_series = df_5m.index.astype(str).str[:10]
                df_5m = df_5m[dt_str_series <= eval_dt_str].copy()

        # If data is insufficient or missing
        if df_5m is None or len(df_5m) < 2:
            health = ShortCoveringDataHealth(
                symbol=symbol,
                in_fo_universe=True,
                health_status="DATA_INSUFFICIENT",
                oi_available=False
            )
            return self._build_record(
                symbol=symbol,
                eval_date=evaluation_date,
                decision="REJECTED",
                reason="DATA_INSUFFICIENT",
                score=0.0,
                health=health,
                mode=mode,
                indicators={"row_count": len(df_5m) if df_5m is not None else 0}
            )

        # 3. Evaluate price and OI delta
        latest_bar = df_5m.iloc[-1]
        prev_bar = df_5m.iloc[-2]
        
        close_p = float(latest_bar.get("Close", latest_bar.get("close", 0.0)))
        prev_close = float(prev_bar.get("Close", prev_bar.get("close", close_p)))
        price_change_pct = ((close_p - prev_close) / prev_close * 100) if prev_close > 0 else 0.0

        # Extract OI if present
        cur_oi = float(latest_bar.get("OI", latest_bar.get("oi", 0.0)))
        prev_oi = float(prev_bar.get("OI", prev_bar.get("oi", cur_oi)))
        oi_change_pct = ((cur_oi - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0.0

        # Short covering condition: Price up (>= +0.75%) + OI unwinding / reduction (<= -1.0%)
        is_short_covering = (price_change_pct >= 0.75 and oi_change_pct <= -1.0)
        decision = "SELECTED" if is_short_covering else "REJECTED"
        reason = "CONFIRMED_SHORT_COVERING" if is_short_covering else "NO_SHORT_COVERING_SIGNATURE"
        score = 85.0 if is_short_covering else 0.0

        health = ShortCoveringDataHealth(
            symbol=symbol,
            in_fo_universe=True,
            futures_contract=f"{symbol}-FUT",
            oi_source="UPSTOX_INTRADAY" if "oi" in latest_bar else "BHAVCOPY_DAILY",
            oi_available=(cur_oi > 0),
            health_status="HEALTHY",
            oi_change_pct=round(oi_change_pct, 2),
            price_change_pct=round(price_change_pct, 2)
        )

        indicators = {
            "Close": close_p,
            "Price_Change_Pct": round(price_change_pct, 2),
            "OI_Change_Pct": round(oi_change_pct, 2),
            "Current_OI": cur_oi
        }

        gates = {
            "PRICE_EXPANSION": GateAuditResult(
                name="PRICE_EXPANSION",
                passed=(price_change_pct >= 0.75),
                status="PASS" if price_change_pct >= 0.75 else "FAIL",
                actual=price_change_pct,
                threshold=0.75,
                operator=">=",
                reason="Price change >= +0.75%" if price_change_pct >= 0.75 else "Price change < +0.75%"
            ),
            "OI_UNWINDING": GateAuditResult(
                name="OI_UNWINDING",
                passed=(oi_change_pct <= -1.0),
                status="PASS" if oi_change_pct <= -1.0 else "FAIL",
                actual=oi_change_pct,
                threshold=-1.0,
                operator="<=",
                reason="OI reduction <= -1.0%" if oi_change_pct <= -1.0 else "No significant OI unwinding"
            )
        }

        return self._build_record(
            symbol=symbol,
            eval_date=evaluation_date,
            decision=decision,
            reason=reason,
            score=score,
            health=health,
            mode=mode,
            indicators=indicators,
            gates=gates,
            df_snap=df_5m
        )

    def _build_record(
        self,
        symbol: str,
        eval_date: str,
        decision: str,
        reason: str,
        score: float,
        health: ShortCoveringDataHealth,
        mode: ReplayMode,
        indicators: Dict[str, Any],
        gates: Optional[Dict[str, GateAuditResult]] = None,
        df_snap: Optional[pd.DataFrame] = None
    ) -> ProductionDecisionRecord:
        snap = FrozenDataSnapshot(
            symbol=symbol,
            row_count=len(df_snap) if df_snap is not None else 0,
            start_date=eval_date,
            end_date=eval_date,
            sha256_hash=get_dataframe_hash(df_snap) if df_snap is not None else "EMPTY",
            timeframe="5m" if self.scanner_type == ScannerType.SHORT_COVERING_5M else "1d"
        )
        return ProductionDecisionRecord(
            symbol=symbol,
            scanner_name=self.scanner_type.value,
            evaluation_date=eval_date,
            evaluation_timestamp=datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
            run_id=f"sc_replay_{int(datetime.now().timestamp())}",
            git_commit=get_git_commit(),
            scanner_file_hash=get_file_hash(os.path.join(_APP_DIR, "short_covering", "short_covering_scanner.py")),
            config_hash="SC_DEFAULT_CONFIG",
            effective_config={},
            market_regime="NEUTRAL",
            data_snapshot=snap,
            indicators=indicators,
            gate_results=gates or {},
            score_breakdown={"score": score},
            final_score=score,
            terminal_decision=decision,
            alert_generated=(decision == "SELECTED"),
            rejection_reason=reason,
            primary_gate=reason,
            replay_mode=mode.value,
            short_covering_health=health
        )
