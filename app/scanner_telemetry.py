import time
import json
import logging
import copy
from typing import Dict, Any, List, Optional
from collections import defaultdict
import datetime
from zoneinfo import ZoneInfo
from config import SCANNER_DECISION_LOGGING

logger = logging.getLogger("ScannerTelemetry")
logger.setLevel(logging.INFO)

class GlobalSystemTelemetry:
    """Singleton to track cross-scanner system state"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.reset()
        return cls._instance

    def reset(self):
        self.symbol_matrix = defaultdict(dict)  # {symbol: {scanner: reason}}
        self.system_summary = {
            "TotalProcessed": 0,
            "TotalPassed": 0,
            "TotalRejected": 0,
            "ScannerBreakdown": defaultdict(int),
            "GateRejections": defaultdict(int)
        }

    def record_decision(self, scanner: str, symbol: str, decision: str, reason: str):
        self.symbol_matrix[symbol][scanner] = "PASS" if decision == "PASS" else reason
        self.system_summary["TotalProcessed"] += 1
        if decision == "PASS":
            self.system_summary["TotalPassed"] += 1
            self.system_summary["ScannerBreakdown"][scanner] += 1
        else:
            self.system_summary["TotalRejected"] += 1
            self.system_summary["GateRejections"][reason] += 1

    def print_system_summary(self):
        if not SCANNER_DECISION_LOGGING:
            return

        lines = [
            "================================================",
            "SYSTEM SUMMARY",
            "================================================",
            f"Total Processed: {self.system_summary['TotalProcessed']}",
            f"Total Alerts   : {self.system_summary['TotalPassed']}",
            "------------------------------------------------",
            "Alerts By Scanner:"
        ]
        
        for scanner, count in sorted(self.system_summary["ScannerBreakdown"].items(), key=lambda x: -x[1]):
            lines.append(f"{scanner:<15}: {count}")

        lines.extend([
            "------------------------------------------------",
            "Most Common Rejections (All Scanners):"
        ])
        
        top_rejections = sorted(self.system_summary["GateRejections"].items(), key=lambda x: -x[1])[:10]
        for gate, count in top_rejections:
            lines.append(f"{gate:<15}: {count}")
            
        lines.append("================================================")
        logger.info("\n".join(lines))


global_telemetry = GlobalSystemTelemetry()


class ScannerDecisionLogger:
    def __init__(self, scanner_name: str, run_id: str, market_regime: str):
        self.scanner_name = scanner_name
        self.run_id = run_id
        self.market_regime = market_regime
        
        self.processed = 0
        self.passed = 0
        self.rejected = 0
        
        self.rejection_counts = defaultdict(int)
        self.near_misses = []
        self.pass_stats = []
        
        self.start_time = time.time()

    def record_decision(
        self,
        symbol: str,
        decision: str,
        last_stage: str,
        gate: str,
        actual: Any,
        required: Any,
        score: float,
        rr: float,
        metadata: Dict[str, Any],
        start_time: float
    ):
        """
        Record exactly ONE terminal decision per symbol.
        """
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        
        self.processed += 1
        if decision == "PASS":
            self.passed += 1
            self.pass_stats.append({"Score": score, "RR": rr, **metadata})
            gate_name = "PASS"
        else:
            self.rejected += 1
            self.rejection_counts[gate] += 1
            gate_name = gate

        global_telemetry.record_decision(self.scanner_name, symbol, decision, gate_name)

        if not SCANNER_DECISION_LOGGING:
            return

        trace_id = f"{self.scanner_name}_{self.run_id}_{symbol}"
        
        log_lines = [
            f"[{self.scanner_name}_DECISION]",
            f"TraceID={trace_id}",
            f"Symbol={symbol}",
            f"Scanner={self.scanner_name}",
            f"RunID={self.run_id}",
            f"MarketRegime={self.market_regime}",
            f"LastStage={last_stage}",
            f"Decision={decision}",
            f"Gate={gate_name}",
            f"Latency={latency_ms}ms"
        ]
        
        if decision == "REJECT":
            log_lines.extend([
                f"Actual={actual}",
                f"Required={required}",
                f"Score={score}",
                f"RR={rr}"
            ])
            
        for k, v in metadata.items():
            log_lines.append(f"{k}={v}")

        logger.info("\n".join(log_lines))

    def record_pass(self, symbol: str, score: float, rr: float, metadata: Dict[str, Any], start_time: float):
        self.record_decision(symbol, "PASS", "FINAL", "PASS", None, None, score, rr, metadata, start_time)

    def record_reject(self, symbol: str, last_stage: str, gate: str, actual: Any, required: Any, score: float = 0, rr: float = 0, metadata: Dict[str, Any] = None, start_time: float = 0):
        metadata = metadata or {}
        
        # Auto-compute near miss for numerics
        if isinstance(actual, (int, float)) and isinstance(required, (int, float)) and required != 0:
            distance = actual - required
            distance_pct = abs(distance / required)
            if distance_pct <= 0.10:
                self.record_near_miss(symbol, gate, round(actual, 2), round(required, 2), round(distance, 2))
                
        self.record_decision(symbol, "REJECT", last_stage, gate, actual, required, score, rr, metadata, start_time)

    def record_near_miss(self, symbol: str, gate: str, actual: float, required: float, missing: float, metadata: Dict[str, Any] = None):
        """Record a near miss separately, does not count as a terminal decision."""
        if not SCANNER_DECISION_LOGGING:
            return
            
        self.near_misses.append({
            "Symbol": symbol,
            "Gate": gate,
            "Actual": actual,
            "Required": required,
            "Missing": missing
        })
        
        metadata = metadata or {}
        log_lines = [
            f"[{self.scanner_name}_NEAR_MISS]",
            f"Symbol={symbol}",
            f"Gate={gate}",
            f"Actual={actual}",
            f"Required={required}",
            f"WouldPassIf={actual}→{required}",
            f"Needed={missing}"
        ]
        for k, v in metadata.items():
            log_lines.append(f"{k}={v}")
        logger.info("\n".join(log_lines))

    def print_summary(self):
        if not SCANNER_DECISION_LOGGING:
            return

        lines = [
            "================================================",
            f"{self.scanner_name} SUMMARY",
            "================================================",
            f"Processed={self.processed}",
            f"Passed={self.passed}",
            f"Rejected={self.rejected}",
        ]
        
        if self.passed == 0:
            lines.extend([
                "",
                "WARNING: Scanner produced zero alerts.",
                f"Top rejection: {max(self.rejection_counts, key=self.rejection_counts.get) if self.rejection_counts else 'None'}",
                f"Near misses: {len(self.near_misses)}",
                "Review thresholds if repeated across multiple sessions."
            ])
        elif self.passed > (self.processed * 0.10) and self.processed > 50:
            lines.extend([
                "",
                "WARNING: Alert count significantly above baseline (>10% hit rate).",
                "Review for possible calibration drift."
            ])
            
        if self.passed > 0:
            avg_score = sum(p.get("Score", 0) for p in self.pass_stats) / self.passed
            avg_rr = sum(p.get("RR", 0) for p in self.pass_stats) / self.passed
            lines.extend([
                "------------------------------------------------",
                "PASS Statistics:",
                f"Average Score: {avg_score:.1f}",
                f"Average RR   : {avg_rr:.2f}"
            ])
            # Averages of numeric metadata
            meta_keys = [k for k in self.pass_stats[0].keys() if k not in ("Score", "RR")]
            for mk in meta_keys:
                try:
                    vals = [float(p[mk]) for p in self.pass_stats if p.get(mk) is not None]
                    if vals:
                        avg_val = sum(vals) / len(vals)
                        lines.append(f"Average {mk}: {avg_val:.2f}")
                except Exception:
                    pass

        if self.rejection_counts:
            lines.extend([
                "------------------------------------------------",
                "Top Rejections:"
            ])
            sorted_rej = sorted(self.rejection_counts.items(), key=lambda x: -x[1])
            for gate, count in sorted_rej[:10]:
                pct = (count / self.processed) * 100 if self.processed else 0
                lines.append(f"{gate:<15}: {count} ({pct:.1f}%)")
                
        if self.near_misses:
            lines.extend([
                "------------------------------------------------",
                "Top Near Misses:"
            ])
            for nm in sorted(self.near_misses, key=lambda x: abs(float(x.get('Missing', 0))))[:5]:
                lines.append(f"{nm['Symbol']:<12} | {nm['Gate']:<10} | {nm['Actual']} / {nm['Required']}")

        lines.append("================================================")
        logger.info("\n".join(lines))
        
        self.save_to_database()
        
    def save_to_database(self):
        try:
            from database import get_connection
            
            top_rej = None
            if self.rejection_counts:
                top_rej = max(self.rejection_counts, key=self.rejection_counts.get)
                
            summary_json = {
                "processed": self.processed,
                "passed": self.passed,
                "rejected": self.rejected,
                "top_rejection": top_rej,
                "near_misses": len(self.near_misses),
                "rejections": dict(self.rejection_counts)
            }
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS scanner_telemetry_history (
                            id SERIAL PRIMARY KEY,
                            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            scanner_name VARCHAR(50),
                            run_id VARCHAR(100),
                            market_regime VARCHAR(50),
                            alerts_generated INT,
                            top_rejection VARCHAR(50),
                            summary_json JSONB
                        )
                    """)
                    cur.execute("""
                        INSERT INTO scanner_telemetry_history 
                        (scanner_name, run_id, market_regime, alerts_generated, top_rejection, summary_json)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (
                        self.scanner_name,
                        self.run_id,
                        self.market_regime,
                        self.passed,
                        top_rej,
                        json.dumps(summary_json)
                    ))
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to save scanner telemetry to database: {e}")
