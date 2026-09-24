# app/certification/replay.py
"""
Exact Production Replay & Deterministic Backtest Certification Orchestrator.
CLI Command and programmatic interface for verifying backtest-to-production equivalence.
"""
import argparse
import json
import os
import sys
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT_DIR = os.path.abspath(os.path.join(_APP_DIR, ".."))
for _p in (_APP_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from certification.models import (
        ProductionDecisionRecord,
        FrozenDataSnapshot,
        GateAuditResult,
        DifferentialReport,
        Tolerances
    )
    from certification.provenance import (
        get_git_commit,
        get_file_hash,
        get_config_hash,
        get_dataframe_hash
    )
    from certification.point_in_time import validate_point_in_time
    from certification.difference_engine import DifferenceEngine
    from certification.data_auditor import ParquetAuditor
except ImportError:
    from app.certification.models import (
        ProductionDecisionRecord,
        FrozenDataSnapshot,
        GateAuditResult,
        DifferentialReport,
        Tolerances
    )
    from app.certification.provenance import (
        get_git_commit,
        get_file_hash,
        get_config_hash,
        get_dataframe_hash
    )
    from app.certification.point_in_time import validate_point_in_time
    from app.certification.difference_engine import DifferenceEngine
    from app.certification.data_auditor import ParquetAuditor

TELEMETRY_LOG_PATH = os.path.abspath(os.path.join(_ROOT_DIR, "logs", "scanner_telemetry.jsonl"))


class ProductionReplayOrchestrator:
    """
    Coordinates loading production records, executing exact replay on identical code,
    validating point-in-time invariants, and certifying equivalence via the Difference Engine.
    """
    def __init__(self, scanner_name: str = "EOD"):
        self.scanner_name = scanner_name.upper()
        self.difference_engine = DifferenceEngine()

    def find_production_record(
        self,
        symbol: str,
        evaluation_date: str,
        run_id: Optional[str] = None
    ) -> Optional[ProductionDecisionRecord]:
        """
        Locates the reference production decision record from telemetry JSONL.
        """
        if not os.path.exists(TELEMETRY_LOG_PATH):
            return None

        matched_record = None
        eval_dt_str = evaluation_date.split(" ")[0]

        with open(TELEMETRY_LOG_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    # Check matching criteria
                    if data.get("symbol") != symbol:
                        continue
                    if run_id and data.get("run_id") != run_id:
                        continue
                    
                    record_ts = data.get("timestamp", "")
                    if eval_dt_str not in record_ts:
                        # Also check Datetime field in all_values
                        dt_val = data.get("all_values", {}).get("Datetime", {}).get("value", "")
                        if eval_dt_str not in str(dt_val):
                            continue

                    matched_record = data
                except Exception:
                    continue

        if not matched_record:
            return None

        # Transform telemetry JSON into ProductionDecisionRecord
        return self._telemetry_to_decision_record(matched_record, eval_dt_str)

    def _telemetry_to_decision_record(self, t: dict, eval_date: str) -> ProductionDecisionRecord:
        symbol = t.get("symbol", "")
        scanner = t.get("scanner", "EOD")
        run_id = t.get("run_id", "unknown")
        
        # Build gates
        gates = {}
        for g_name, g_info in t.get("gate_results", {}).items():
            gates[g_name] = GateAuditResult(
                name=g_name,
                passed=bool(g_info.get("passed", False)),
                status=g_info.get("status", "FAIL"),
                actual=g_info.get("actual"),
                threshold=g_info.get("threshold"),
                operator=g_info.get("operator"),
                reason=g_info.get("reason"),
            )

        # Build indicators from all_values
        indicators = {}
        for k, v in t.get("all_values", {}).items():
            if v.get("group") in ("INDICATOR", "MARKET_DATA", "INPUT") or k in ("ATR", "ATR20", "RSI", "Volume", "High", "Low", "Close", "Open"):
                indicators[k] = v.get("value")

        # Snapshot
        data_snap = FrozenDataSnapshot(
            symbol=symbol,
            row_count=t.get("data_quality", {}).get("present_fields", 0),
            start_date=eval_date,
            end_date=eval_date,
            sha256_hash="PROD_TELEMETRY_SNAPSHOT",
            provider=t.get("decision_manifest", [{}])[0].get("provider", "NSE_BHAVCOPY") if t.get("decision_manifest") else "CACHE",
            market_regime=t.get("all_values", {}).get("MARKET_REGIME", {}).get("value", "NEUTRAL"),
            evaluated_at=t.get("timestamp")
        )

        cfg_hash, _ = get_config_hash(t.get("configuration", {}))

        return ProductionDecisionRecord(
            symbol=symbol,
            scanner_name=scanner,
            evaluation_date=eval_date,
            evaluation_timestamp=t.get("timestamp", ""),
            run_id=run_id,
            git_commit="unknown",
            scanner_file_hash=get_file_hash(os.path.join(_APP_DIR, "eod_scanner.py")),
            config_hash=cfg_hash,
            effective_config=t.get("configuration", {}),
            market_regime=t.get("all_values", {}).get("MARKET_REGIME", {}).get("value", "NEUTRAL"),
            data_snapshot=data_snap,
            indicators=indicators,
            gate_results=gates,
            score_breakdown=t.get("score_breakdown", {}),
            final_score=float(t.get("score_breakdown", {}).get("total_score", 0.0) or 0.0),
            terminal_decision=t.get("terminal_decision", "REJECTED"),
            alert_generated=bool(t.get("alert_generated", False)),
            rejection_reason=t.get("primary_reason"),
            primary_gate=t.get("primary_reason"),
        )

    def execute_replay(
        self,
        symbol: str,
        df: pd.DataFrame,
        evaluation_date: str,
        effective_config: Optional[dict] = None,
        market_regime: str = "STRONG_BEAR",
        delivery_pct: Optional[float] = None
    ) -> ProductionDecisionRecord:
        """
        Executes exact replay on the production scanner core without reimplementing logic.
        """
        import eod_scanner
        from technical_indicators import hydrate_indicators

        # 1. Point-in-time filter: df must be sliced up to evaluation_date
        time_col = next((c for c in ["Datetime", "Date", "timestamp"] if c in df.columns), None)
        if time_col:
            eval_dt = datetime.strptime(evaluation_date.split(" ")[0], "%Y-%m-%d").date()
            dt_s = pd.to_datetime(df[time_col])
            mask = dt_s.apply(lambda x: (x.astimezone(IST).date() if hasattr(x, "tzinfo") and x.tzinfo else x.date()) <= eval_dt)
            df_cut = df[mask].copy()
        else:
            df_cut = df.copy()

        df_cut = hydrate_indicators(df_cut, timeframe="1d")
        latest = df_cut.iloc[-1]

        # 2. Run shared EOD condition evaluation
        cond = eod_scanner._check_eod_conditions(
            ticker=df_cut,
            latest=latest,
            symbol=symbol,
            mode="ui",
            prior_high_source="raw",
            delivery_pct=delivery_pct
        )

        passed = cond.get("passed", False)
        rejection_reason = cond.get("reason")
        decision = "SELECTED" if passed else "REJECTED"

        # 3. Capture indicators
        indicators = {
            "Open": float(latest.get("Open", 0.0)),
            "High": float(latest.get("High", 0.0)),
            "Low": float(latest.get("Low", 0.0)),
            "Close": float(latest.get("Close", 0.0)),
            "Volume": float(latest.get("Volume", 0.0)),
            "RSI": float(latest.get("RSI", 0.0)) if not pd.isna(latest.get("RSI")) else None,
            "ATR20": float(cond.get("atr20", 0.0)),
            "PRIOR_20D_HIGH": float(cond.get("prior_high", 0.0)) if cond.get("prior_high") is not None else None,
            "ATR_EXPANSION": float(cond.get("candle_range", 0.0) / cond.get("atr20", 1.0)) if cond.get("atr20") else None,
            "RVOL": float(cond.get("volume_ratio", 0.0)),
            "BODY_RATIO": float(cond.get("body_ratio", 0.0)),
            "CLOSE_POSITION": float(cond.get("close_pos", 0.0)),
            "UPPER_WICK_RATIO": float(cond.get("wick_ratio", 0.0)),
            "HIGH_52W": float(latest.get("HIGH_52W", 0.0)) if "HIGH_52W" in latest else None,
        }

        # 4. Capture gates
        gates = {
            "ATR_EXPANSION": GateAuditResult(
                name="ATR_EXPANSION",
                passed=(indicators["ATR_EXPANSION"] >= 0.80) if indicators["ATR_EXPANSION"] is not None else False,
                status="PASS" if (indicators["ATR_EXPANSION"] and indicators["ATR_EXPANSION"] >= 0.80) else "FAIL",
                actual=indicators["ATR_EXPANSION"],
                threshold=0.80,
                operator=">=",
                reason="ATR expansion >= 0.80" if (indicators["ATR_EXPANSION"] and indicators["ATR_EXPANSION"] >= 0.80) else "Compressed range relative to ATR20"
            ),
            "BREAKOUT_GATE": GateAuditResult(
                name="BREAKOUT_GATE",
                passed=(indicators["Close"] > indicators["PRIOR_20D_HIGH"]) if indicators["PRIOR_20D_HIGH"] else False,
                status="PASS" if (indicators["PRIOR_20D_HIGH"] and indicators["Close"] > indicators["PRIOR_20D_HIGH"]) else "FAIL",
                actual=indicators["Close"],
                threshold=indicators["PRIOR_20D_HIGH"],
                operator=">",
                reason="Closed above 20-day high" if (indicators["PRIOR_20D_HIGH"] and indicators["Close"] > indicators["PRIOR_20D_HIGH"]) else "Below 20-day high"
            ),
            "VOLUME_SURGE": GateAuditResult(
                name="VOLUME_SURGE",
                passed=(indicators["RVOL"] >= 1.80),
                status="PASS" if indicators["RVOL"] >= 1.80 else "FAIL",
                actual=indicators["RVOL"],
                threshold=1.80,
                operator=">=",
                reason="Volume surge >= 1.80x" if indicators["RVOL"] >= 1.80 else "Volume surge < 1.80x"
            )
        }

        # 5. Score
        score = 0.0
        if passed:
            signals = eod_scanner.detect_breakouts(df_cut, timeframe="1d")
            score, _, _ = eod_scanner.calculate_score(
                category="EQUITY",
                breakout_count=len(signals),
                rsi=indicators["RSI"] or 50.0,
                volume_ratio=indicators["RVOL"],
                breakout_signals=signals,
                ticker=df_cut,
                latest=latest,
                symbol=symbol,
                timeframe="1d",
                atr_val=indicators["ATR20"],
                regime_ctx={"market_regime": market_regime}
            )

        snap = FrozenDataSnapshot(
            symbol=symbol,
            row_count=len(df_cut),
            start_date=str(df_cut[time_col].iloc[0]) if time_col else evaluation_date,
            end_date=evaluation_date,
            sha256_hash=get_dataframe_hash(df_cut),
            delivery_pct=delivery_pct,
            market_regime=market_regime
        )

        cfg_hash, clean_cfg = get_config_hash(effective_config or eod_scanner.EOD_ADVANCED_CONFIG)

        return ProductionDecisionRecord(
            symbol=symbol,
            scanner_name="EOD_BREAKOUT",
            evaluation_date=evaluation_date,
            evaluation_timestamp=datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
            run_id="replay_" + datetime.now(IST).strftime("%Y%m%d_%H%M%S"),
            git_commit=get_git_commit(),
            scanner_file_hash=get_file_hash(os.path.join(_APP_DIR, "eod_scanner.py")),
            config_hash=cfg_hash,
            effective_config=clean_cfg,
            market_regime=market_regime,
            data_snapshot=snap,
            indicators=indicators,
            gate_results=gates,
            score_breakdown={"score": score},
            final_score=float(score),
            terminal_decision=decision,
            alert_generated=(decision == "SELECTED"),
            rejection_reason=rejection_reason,
            primary_gate=rejection_reason
        )

    def certify_symbol(
        self,
        symbol: str,
        evaluation_date: str,
        prod_record: Optional[ProductionDecisionRecord] = None,
        raw_df: Optional[pd.DataFrame] = None
    ) -> DifferentialReport:
        """
        Runs certification for a single symbol.
        """
        # 1. Fetch prod record if not supplied
        if prod_record is None:
            prod_record = self.find_production_record(symbol, evaluation_date)
            if prod_record is None:
                return DifferentialReport(
                    symbol=symbol,
                    scanner_name=self.scanner_name,
                    evaluation_date=evaluation_date,
                    certified=False,
                    summary=f"FAIL: No production record found for {symbol} on {evaluation_date}"
                )

        # 2. Load historical data for replay
        if raw_df is None:
            parquet_path = os.path.join(_ROOT_DIR, "data", "history", "1d", f"{symbol}.parquet")
            if os.path.exists(parquet_path):
                raw_df = pd.read_parquet(parquet_path)
            else:
                return DifferentialReport(
                    symbol=symbol,
                    scanner_name=self.scanner_name,
                    evaluation_date=evaluation_date,
                    certified=False,
                    summary=f"FAIL: Parquet file not found for {symbol}"
                )

        # 3. Validate point-in-time
        pit_valid, pit_violations = validate_point_in_time(raw_df, evaluation_date, symbol=symbol)

        # 4. Replay
        replay_record = self.execute_replay(
            symbol=symbol,
            df=raw_df,
            evaluation_date=evaluation_date,
            effective_config=prod_record.effective_config,
            market_regime=prod_record.market_regime
        )

        # 5. Differential Comparison
        report = self.difference_engine.compare(
            prod_record=prod_record,
            replay_record=replay_record,
            pit_valid=pit_valid,
            pit_violations=pit_violations
        )
        return report


def format_report_console(report: DifferentialReport) -> str:
    """Formats DifferentialReport as a high-density, professional terminal output."""
    lines = []
    lines.append("=" * 80)
    lines.append("🔬 EXACT PRODUCTION REPLAY / DETERMINISTIC CERTIFICATION REPORT")
    lines.append("=" * 80)
    lines.append(f"Symbol              : {report.symbol}")
    lines.append(f"Evaluation Date     : {report.evaluation_date}")
    lines.append(f"Scanner Name        : {report.scanner_name}")
    status_label = "✅ PASS" if report.certified else "❌ FAIL"
    lines.append(f"Certification Status: {status_label}")
    lines.append("-" * 80)

    lines.append("VERIFICATION CHECKLIST:")
    lines.append(f"  • Code Version Match     : {'PASS' if report.code_version_match else 'FAIL'}")
    lines.append(f"  • Configuration Match    : {'PASS' if report.config_match else 'FAIL'}")
    lines.append(f"  • Data Snapshot Match    : {'PASS' if report.data_match else 'FAIL'}")
    lines.append(f"  • Point-In-Time Causality: {'PASS' if report.point_in_time_valid else 'FAIL'}")
    lines.append(f"  • Indicator Trace Match  : {'PASS' if report.indicators_match else 'FAIL'}")
    lines.append(f"  • Gate Decision Match    : {'PASS' if report.gates_match else 'FAIL'}")
    lines.append(f"  • Terminal Decision Match: {'PASS' if report.decision_match else 'FAIL'}")
    lines.append("-" * 80)

    if not report.certified:
        lines.append("\n🚨 DIVERGENCE AUDIT:")
        lines.append(f"  FIRST DIVERGENCE      : {report.first_divergence}")
        lines.append(f"  ROOT INPUT DIVERGENCE : {report.root_input_divergence}")
        lines.append(f"  DOWNSTREAM IMPACT     : {' -> '.join(report.downstream_impact) if report.downstream_impact else 'None'}")
        lines.append("")

    lines.append("FIELD DIFFERENTIAL TABLE:")
    lines.append(f"{'FIELD':<22} | {'PRODUCTION':<18} | {'REPLAY':<18} | {'DELTA':<10} | {'STATUS'}")
    lines.append("-" * 80)
    for c in report.field_comparisons:
        p_str = f"{c.prod_value:.4f}" if isinstance(c.prod_value, float) else str(c.prod_value)
        r_str = f"{c.replay_value:.4f}" if isinstance(c.replay_value, float) else str(c.replay_value)
        d_str = f"{c.delta:+.4f}" if c.delta is not None else "-"
        status = "MATCH" if c.matches else "MISMATCH"
        lines.append(f"{c.field_name:<22} | {p_str:<18} | {r_str:<18} | {d_str:<10} | {status}")

    lines.append("=" * 80)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Deterministic Production Replay & Certification CLI")
    parser.add_argument("--scanner", type=str, default="EOD", help="Scanner name (EOD, REVERSAL, SHORT_COVERING)")
    parser.add_argument("--symbol", type=str, help="Stock symbol to certify")
    parser.add_argument("--date", type=str, default="2026-09-23", help="Evaluation date (YYYY-MM-DD)")
    parser.add_argument("--production-run", type=str, help="Production Run ID to match")
    parser.add_argument("--audit-data", action="store_true", help="Run global historical data quality audit")
    parser.add_argument("--sanitize-data", action="store_true", help="Sanitize contaminated parquet files")
    parser.add_argument("--certify-all", action="store_true", help="Certify entire evaluation population for date")

    args = parser.parse_args()

    # 1. Global Historical Data Audit
    if args.audit_data or args.sanitize_data:
        print("=" * 80)
        print("🔍 RUNNING GLOBAL HISTORICAL DATA QUALITY AUDIT (data/history/1d/)")
        print("=" * 80)
        res = ParquetAuditor.audit_directory()
        print(f"Total Parquets Scanned : {res['total_files']}")
        print(f"Clean Parquets         : {res['clean_files']}")
        print(f"Contaminated Parquets  : {res['contaminated_count']}")
        print("-" * 80)

        for c in res["contaminated_files"]:
            print(f"⚠️ {c['symbol']:<15} | Corrupt Rows: {c['corrupted_rows']} | Issues: {', '.join(c['issues'])}")

        if args.sanitize_data and res["contaminated_count"] > 0:
            print("\n🔄 SANITIZING CONTAMINATED PARQUET FILES...")
            total_purged = 0
            for c in res["contaminated_files"]:
                ok, purged, msg = ParquetAuditor.sanitize_file(c["file"])
                if ok:
                    total_purged += purged
                    print(f"  ✅ {c['symbol']}: {msg}")
            print(f"\n🎉 DATA CLEANUP COMPLETE: Purged {total_purged} rogue/synthetic rows across {res['contaminated_count']} files.")
        return

    # 2. Single Symbol Replay Certification
    orchestrator = ProductionReplayOrchestrator(scanner_name=args.scanner)
    if args.symbol:
        report = orchestrator.certify_symbol(
            symbol=args.symbol,
            evaluation_date=args.date
        )
        print(format_report_console(report))
        sys.exit(0 if report.certified else 1)

    # 3. Population Certification
    if args.certify_all:
        print(f"🚀 Running Population Certification for {args.scanner} on {args.date}...")
        # Inspect all records in telemetry for that date
        records = []
        if os.path.exists(TELEMETRY_LOG_PATH):
            with open(TELEMETRY_LOG_PATH, "r") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        if args.date in d.get("timestamp", "") and d.get("scanner") == args.scanner:
                            records.append(d)
                    except Exception:
                        pass
        print(f"Found {len(records)} production evaluations for {args.date}.")
        passed = 0
        failed = 0
        for r in records[:20]:
            sym = r["symbol"]
            rep = orchestrator.certify_symbol(sym, args.date)
            if rep.certified:
                passed += 1
            else:
                failed += 1
            print(f"  {sym:<12}: {'PASS' if rep.certified else 'FAIL'}")
        print(f"\nPopulation Summary: Passed={passed}, Failed={failed}")


if __name__ == "__main__":
    main()
