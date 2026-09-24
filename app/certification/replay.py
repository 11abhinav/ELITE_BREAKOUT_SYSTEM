# app/certification/replay.py
"""
Master Exact Production Replay & Deterministic Backtest Certification Orchestrator.
Supports all production scanners via Registry, dual replay modes (PRODUCTION_REPLAY vs CLEAN_HISTORICAL_REPLAY),
and Master Certification Matrix generation.
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

from app.certification.models import (
    ProductionDecisionRecord,
    FrozenDataSnapshot,
    GateAuditResult,
    DifferentialReport,
    ReplayMode,
    ScannerCertificationStatus,
    ScannerMatrixRow,
    ScannerType,
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
from app.certification.registry import ScannerCertificationRegistry

TELEMETRY_LOG_PATH = os.path.abspath(os.path.join(_ROOT_DIR, "logs", "scanner_telemetry.jsonl"))

SCANNER_NAME_ALIASES: Dict[str, List[str]] = {
    "EOD": ["EOD", "EOD_BREAKOUT"],
    "EOD_BREAKOUT": ["EOD", "EOD_BREAKOUT"],
    "MULTITF_15M": ["MULTI_TF", "MULTITF_15M", "MULTITF"],
    "MULTITF_5M": ["MULTI_TF", "MULTITF_5M"],
    "SHORT_COVERING_EOD": ["SHORT_COVERING_EOD", "SHORT_COVERING"],
    "SHORT_COVERING_5M": ["SHORT_COVERING_5M"],
    "REVERSAL": ["REVERSAL", "REVERSAL_V2"],
    "PULLBACK": ["PULLBACK"],
    "TECHNICAL": ["TECHNICAL"],
    "ACCUMULATION_VCP": ["ACCUMULATION_VCP", "VCP"],
    "INSTITUTIONAL_ACCUMULATION": ["INSTITUTIONAL_ACCUMULATION", "ACCUMULATION"],
    "WEALTH": ["WEALTH_ENGINE", "WEALTH"],
    "MULTIBAGGER": ["MULTIBAGGER"],
    "DAILY_BUILDER": ["DAILY_BUILDER", "BUILDER"],
}


class ProductionReplayOrchestrator:
    """
    Coordinates multi-scanner certification across dual modes:
    Mode 1: PRODUCTION_REPLAY -> Proves reproducibility of actual production execution.
    Mode 2: CLEAN_HISTORICAL_REPLAY -> Generates benchmark execution on sanitized historical data.
    """
    def __init__(self, scanner_name: str = "EOD"):
        self.scanner_name = scanner_name.upper()
        self.adapter = ScannerCertificationRegistry.get_adapter(self.scanner_name)
        if not self.adapter:
            # Fallback to EOD
            self.adapter = ScannerCertificationRegistry.get_adapter("EOD")
        self.difference_engine = DifferenceEngine()

    def find_production_record(
        self,
        symbol: str,
        evaluation_date: str,
        run_id: Optional[str] = None
    ) -> Optional[ProductionDecisionRecord]:
        """Locates reference production record from telemetry stream for this scanner."""
        if not os.path.exists(TELEMETRY_LOG_PATH):
            return None

        matched = None
        eval_dt_str = evaluation_date.split(" ")[0]
        allowed_scanners = [s.upper() for s in SCANNER_NAME_ALIASES.get(self.scanner_name.upper(), [self.scanner_name.upper()])]

        with open(TELEMETRY_LOG_PATH, "r") as f:
            for line in f:
                if f'"{symbol}"' not in line or eval_dt_str not in line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("symbol") != symbol:
                        continue
                    rec_scanner = str(data.get("scanner", "")).upper()
                    if rec_scanner not in allowed_scanners:
                        continue
                    if run_id and data.get("run_id") != run_id:
                        continue
                    
                    record_ts = data.get("timestamp", "")
                    dt_val = data.get("all_values", {}).get("Datetime", {}).get("value", "")
                    if eval_dt_str not in record_ts and eval_dt_str not in str(dt_val):
                        continue

                    matched = data
                except Exception:
                    continue

        if not matched:
            return None

        return self._telemetry_to_decision_record(matched, eval_dt_str)

    def _telemetry_to_decision_record(self, t: dict, eval_date: str) -> ProductionDecisionRecord:
        symbol = t.get("symbol", "")
        scanner = t.get("scanner", self.scanner_name)
        run_id = t.get("run_id", "unknown")
        
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

        indicators = {}
        for k, v in t.get("all_values", {}).items():
            if k in ("Datetime", "Date", "timestamp", "Time", "MARKET_REGIME") or k.startswith("GATE_"):
                continue
            if v.get("group") in ("INDICATOR", "MARKET_DATA", "INPUT") or k in ("ATR", "ATR20", "RSI", "Volume", "High", "Low", "Close", "Open"):
                indicators[k] = v.get("value")

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

        cfg_hash, clean_cfg = get_config_hash(t.get("configuration", {}))

        return ProductionDecisionRecord(
            symbol=symbol,
            scanner_name=scanner,
            evaluation_date=eval_date,
            evaluation_timestamp=t.get("timestamp", ""),
            run_id=run_id,
            git_commit="unknown",
            scanner_file_hash="scanner_hash",
            config_hash=cfg_hash,
            effective_config=clean_cfg,
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
            replay_mode=ReplayMode.PRODUCTION_REPLAY.value
        )

    def certify_symbol(
        self,
        symbol: str,
        evaluation_date: str,
        mode: ReplayMode = ReplayMode.PRODUCTION_REPLAY,
        prod_record: Optional[ProductionDecisionRecord] = None,
        custom_data: Optional[Dict[str, Any]] = None
    ) -> DifferentialReport:
        """
        Executes certification under the specified ReplayMode.
        """
        # 1. Fetch reference production record
        if prod_record is None:
            prod_record = self.find_production_record(symbol, evaluation_date)
            if prod_record is None:
                # Per Rule 9: Missing telemetry MUST be treated as a certification failure (PENDING / TELEMETRY_REQUIRED).
                # Never assume equivalence by synthesizing an artificial baseline.
                prod_record = ProductionDecisionRecord(
                    symbol=symbol,
                    scanner_name=self.scanner_name,
                    evaluation_date=evaluation_date,
                    evaluation_timestamp="",
                    run_id="MISSING_TELEMETRY",
                    git_commit="unknown",
                    scanner_file_hash="unknown",
                    config_hash="MISSING_TELEMETRY",
                    effective_config={},
                    market_regime="UNKNOWN",
                    data_snapshot=FrozenDataSnapshot(
                        symbol=symbol,
                        row_count=0,
                        start_date=evaluation_date,
                        end_date=evaluation_date,
                        sha256_hash="MISSING_TELEMETRY",
                        provider="NONE"
                    ),
                    indicators={},
                    gate_results={},
                    score_breakdown={},
                    final_score=0.0,
                    terminal_decision="UNVERIFIED",
                    alert_generated=False,
                    rejection_reason="NO_PRODUCTION_TELEMETRY_LOGGED",
                    primary_gate="MISSING_TELEMETRY",
                    replay_mode=mode.value
                )

        # 2. Run Replay through registered adapter
        replay_record = self.adapter.evaluate(
            symbol=symbol,
            evaluation_date=evaluation_date,
            mode=mode,
            prod_record=prod_record,
            custom_data=custom_data
        )

        # 3. Check point-in-time causality
        raw_df = custom_data.get("df") if custom_data else None
        if raw_df is None:
            p = os.path.join(_ROOT_DIR, "data", "history", "1d", f"{symbol}.parquet")
            if os.path.exists(p):
                raw_df = pd.read_parquet(p)

        pit_valid, pit_violations = validate_point_in_time(raw_df, evaluation_date, symbol=symbol)

        # 4. Compare via Difference Engine
        report = self.difference_engine.compare(
            prod_record=prod_record,
            replay_record=replay_record,
            pit_valid=pit_valid,
            pit_violations=pit_violations
        )
        report.replay_mode = mode.value
        return report

    @classmethod
    def generate_matrix(cls, evaluation_date: str = "2026-09-23") -> List[ScannerMatrixRow]:
        """
        Generates the Master Scanner Certification Matrix across all registered scanners.
        """
        rows = []
        scanners = [
            ("EOD Breakout", "EOD", "1D"),
            ("Multi-TF 15M", "MULTITF_15M", "15M"),
            ("Multi-TF 5M", "MULTITF_5M", "5M"),
            ("Short Covering EOD", "SHORT_COVERING_EOD", "1D"),
            ("Short Covering 5M", "SHORT_COVERING_5M", "5M"),
            ("Reversal", "REVERSAL", "1D"),
            ("Pullback", "PULLBACK", "1D"),
            ("Technical", "TECHNICAL", "1D"),
            ("Accumulation / VCP", "ACCUMULATION_VCP", "1D"),
            ("Institutional Accumulation", "INSTITUTIONAL_ACCUMULATION", "1D"),
            ("Wealth Engine", "WEALTH", "1D"),
            ("Multibagger Engine", "MULTIBAGGER", "1D"),
            ("Daily Builder", "DAILY_BUILDER", "1D"),
        ]

        test_symbols = ["PGIL", "SAMHI", "INDRAMEDCO", "ACE", "POWERGRID"]

        for label, sc_name, tf in scanners:
            orch = cls(scanner_name=sc_name)
            prod_cases = 0
            replay_cases = 0
            decision_matches = 0
            trace_matches = 0
            missing_telemetry_count = 0

            # EOD production run c1f7a76e-ae32-41a7-93fd-ce5756da9ae9 evaluated 298 symbols
            # including PGIL, HBLENGINE, INDRAMEDCO, ACE, POWERGRID
            current_symbols = ["PGIL", "HBLENGINE", "INDRAMEDCO", "ACE", "POWERGRID"] if sc_name in ("EOD", "EOD_BREAKOUT") else test_symbols

            for sym in current_symbols:
                rep = orch.certify_symbol(sym, evaluation_date, mode=ReplayMode.PRODUCTION_REPLAY)
                prod_cases += 1
                replay_cases += 1
                if rep.primary_mismatch_category == "MISSING_TELEMETRY":
                    missing_telemetry_count += 1
                else:
                    if rep.decision_match:
                        decision_matches += 1
                    if rep.certified:
                        trace_matches += 1

            dec_pct = round(decision_matches / prod_cases * 100.0, 1) if prod_cases else 0.0
            trace_pct = round(trace_matches / prod_cases * 100.0, 1) if prod_cases else 0.0
            
            if missing_telemetry_count > 0:
                status = "PENDING / TELEMETRY_REQUIRED"
            elif trace_pct == 100.0 and dec_pct == 100.0:
                status = "CERTIFIED"
            elif dec_pct >= 80.0:
                status = "PARTIAL"
            else:
                status = "NOT_CERTIFIED"

            rows.append(ScannerMatrixRow(
                scanner=label,
                mode=tf,
                production_cases=prod_cases,
                replay_cases=replay_cases,
                trace_match_pct=trace_pct,
                decision_match_pct=dec_pct,
                status=status
            ))

        return rows


def format_report_console(report: DifferentialReport) -> str:
    """Formats DifferentialReport as a high-density, professional terminal output."""
    lines = []
    lines.append("=" * 80)
    lines.append("🔬 EXACT PRODUCTION REPLAY / DETERMINISTIC CERTIFICATION REPORT")
    lines.append("=" * 80)
    lines.append(f"Symbol              : {report.symbol}")
    lines.append(f"Evaluation Date     : {report.evaluation_date}")
    lines.append(f"Scanner Name        : {report.scanner_name}")
    lines.append(f"Replay Mode         : {report.replay_mode}")
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


def format_matrix_console(rows: List[ScannerMatrixRow]) -> str:
    """Formats the Master Scanner Certification Matrix."""
    lines = []
    lines.append("=" * 95)
    lines.append("📊 MASTER SCANNER PRODUCTION REPLAY CERTIFICATION MATRIX")
    lines.append("=" * 95)
    lines.append(f"{'SCANNER':<24} | {'MODE':<6} | {'PROD':<5} | {'REPLAY':<6} | {'TRACE MATCH':<12} | {'DECISION':<10} | {'STATUS'}")
    lines.append("-" * 95)
    
    total_scanners = len(rows)
    certified_count = 0
    partial_count = 0

    for r in rows:
        if r.status == "CERTIFIED":
            certified_count += 1
        elif r.status == "PARTIAL":
            partial_count += 1
        t_str = f"{r.trace_match_pct:.1f}%"
        d_str = f"{r.decision_match_pct:.1f}%"
        lines.append(f"{r.scanner:<24} | {r.mode:<6} | {r.production_cases:<5} | {r.replay_cases:<6} | {t_str:<12} | {d_str:<10} | {r.status}")

    lines.append("-" * 95)
    
    overall = "FULLY CERTIFIED" if certified_count == total_scanners else ("PARTIALLY CERTIFIED" if certified_count > 0 or partial_count > 0 else "NOT CERTIFIED")
    lines.append(f"OVERALL SYSTEM STATUS : {overall} ({certified_count}/{total_scanners} Fully Certified, {partial_count} Partial)")
    lines.append("=" * 95)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Deterministic Production Replay & Certification CLI")
    parser.add_argument("--scanner", type=str, default="EOD", help="Scanner name (EOD, MULTITF, SHORT_COVERING, REVERSAL, PULLBACK, TECHNICAL, VCP)")
    parser.add_argument("--symbol", type=str, help="Stock symbol to certify")
    parser.add_argument("--date", type=str, default="2026-09-23", help="Evaluation date (YYYY-MM-DD)")
    parser.add_argument("--mode", type=str, default="PRODUCTION_REPLAY", choices=["PRODUCTION_REPLAY", "CLEAN_HISTORICAL_REPLAY"], help="Replay mode")
    parser.add_argument("--audit-data", action="store_true", help="Run global historical data quality audit")
    parser.add_argument("--sanitize-data", action="store_true", help="Sanitize contaminated parquet files")
    parser.add_argument("--certify-matrix", action="store_true", help="Generate Master Scanner Certification Matrix across all scanners")

    args = parser.parse_args()

    # 1. Global Historical Data Audit across all timeframes
    if args.audit_data or args.sanitize_data:
        print("=" * 80)
        print("🔍 RUNNING GLOBAL HISTORICAL DATA QUALITY AUDIT ACROSS ALL TIMEFRAMES")
        print("=" * 80)
        results = ParquetAuditor.audit_all_historical_stores()
        print(f"{'DATASET':<10} | {'FILES':<8} | {'TOTAL ROWS':<12} | {'CLEAN':<8} | {'CORRUPTED':<10} | {'STATUS'}")
        print("-" * 75)
        for r in results:
            c_cnt = r['corrupted_count']
            status = 'CLEAN' if c_cnt == 0 else f'CONTAMINATED ({c_cnt})'
            print(f"{r['timeframe'].upper():<10} | {r['total_files']:<8} | {r['total_rows']:<12} | {r['clean_files']:<8} | {r['corrupted_count']:<10} | {status}")
        return

    # 2. Master Certification Matrix across all scanners
    if args.certify_matrix:
        rows = ProductionReplayOrchestrator.generate_matrix(evaluation_date=args.date)
        print(format_matrix_console(rows))
        return

    # 3. Single Symbol Certification
    orchestrator = ProductionReplayOrchestrator(scanner_name=args.scanner)
    if args.symbol:
        replay_mode = ReplayMode(args.mode)
        report = orchestrator.certify_symbol(
            symbol=args.symbol,
            evaluation_date=args.date,
            mode=replay_mode
        )
        print(format_report_console(report))
        sys.exit(0 if report.certified else 1)


if __name__ == "__main__":
    main()
