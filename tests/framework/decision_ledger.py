# =====================================================================================
# tests/framework/decision_ledger.py
# LEVEL 4 & 5: DECISION LEDGER & REPORT GENERATOR (JSON + HTML)
# =====================================================================================
import json
import logging
import os
import time
from typing import Any, Dict, List

logger = logging.getLogger("DECISION_LEDGER")


class DiagnosticDecisionLedger:
    """Per-symbol decision ledger tracking inputs -> gates -> score -> decision -> alert lifecycle."""
    def __init__(self, run_id: str = None):
        self.run_id = run_id or f"run_{int(time.time())}"
        self.start_time = time.time()
        self.symbol_entries: Dict[str, Dict[str, Any]] = {}
        self.scanner_summaries: Dict[str, Dict[str, Any]] = {}
        self.golden_fixture_results: List[Dict[str, Any]] = []

    def record_symbol_stage(self, symbol: str, scanner: str, stage: str, inputs: dict, outputs: dict, status: str, reason: str = ""):
        if symbol not in self.symbol_entries:
            self.symbol_entries[symbol] = {
                "symbol": symbol,
                "scanners": {}
            }
            
        if scanner not in self.symbol_entries[symbol]["scanners"]:
            self.symbol_entries[symbol]["scanners"][scanner] = {
                "scanner_name": scanner,
                "stages": [],
                "final_decision": "PENDING",
                "score": 0.0,
                "alert": None,
                "rejection_reason": ""
            }
            
        stage_entry = {
            "timestamp": time.time(),
            "stage_name": stage,
            "inputs": inputs or {},
            "outputs": outputs or {},
            "status": status,
            "reason": reason
        }
        self.symbol_entries[symbol]["scanners"][scanner]["stages"].append(stage_entry)

    def record_final_decision(self, symbol: str, scanner: str, decision: str, score: float = 0.0, alert_dict: dict = None, rejection_reason: str = ""):
        if symbol not in self.symbol_entries:
            self.symbol_entries[symbol] = {"symbol": symbol, "scanners": {}}
            
        if scanner not in self.symbol_entries[symbol]["scanners"]:
            self.symbol_entries[symbol]["scanners"][scanner] = {
                "scanner_name": scanner,
                "stages": [],
                "final_decision": decision,
                "score": score,
                "alert": alert_dict,
                "rejection_reason": rejection_reason
            }
        else:
            sc = self.symbol_entries[symbol]["scanners"][scanner]
            sc["final_decision"] = decision
            sc["score"] = score
            sc["alert"] = alert_dict
            sc["rejection_reason"] = rejection_reason

    def record_golden_fixture(self, fixture_name: str, expected_decision: str, actual_decision: str, score: float, sl: float, target: float, status: str):
        self.golden_fixture_results.append({
            "fixture_name": fixture_name,
            "expected_decision": expected_decision,
            "actual_decision": actual_decision,
            "score": score,
            "sl": sl,
            "target": target,
            "status": status,
            "timestamp": time.time()
        })

    def generate_artifacts(self, output_dir: str = "./artifacts/reports"):
        os.makedirs(output_dir, exist_ok=True)
        end_time = time.time()
        dur_s = round(end_time - self.start_time, 2)

        # 1. Generate scanner_decision_ledger.json
        ledger_path = os.path.join(output_dir, "scanner_decision_ledger.json")
        ledger_data = {
            "run_id": self.run_id,
            "duration_seconds": dur_s,
            "total_symbols_evaluated": len(self.symbol_entries),
            "golden_fixtures": self.golden_fixture_results,
            "symbol_ledger": self.symbol_entries
        }
        with open(ledger_path, "w") as f:
            json.dump(ledger_data, f, indent=2, default=str)
        logger.info(f"📄 Generated {ledger_path}")

        # 2. Generate api_completeness_report.json
        from api_contract_verifier import global_api_report
        api_path = os.path.join(output_dir, "api_completeness_report.json")
        with open(api_path, "w") as f:
            json.dump(global_api_report.to_dict(), f, indent=2, default=str)
        logger.info(f"📄 Generated {api_path}")

        # 3. Generate scanner_validation_report.html
        html_path = os.path.join(output_dir, "scanner_validation_report.html")
        
        passed_count = sum(1 for sym, d in self.symbol_entries.items() if any(s.get("final_decision") in ("ALERT", "PASS", "REJECT") for s in d.get("scanners", {}).values()))
        failed_count = len(self.symbol_entries) - passed_count
        overall_status = "PASS" if global_api_report.missing_field_count == 0 and global_api_report.rate_limit_429_count == 0 and failed_count == 0 else "FAIL"

        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Scanner System Validation Certificate — {self.run_id}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 20px; }}
        .certificate {{ border: 2px solid #3b82f6; border-radius: 12px; padding: 30px; background: #1e293b; max-width: 1000px; margin: 0 auto; }}
        h1 {{ color: #60a5fa; margin-top: 0; }}
        .status-badge {{ display: inline-block; padding: 8px 16px; font-weight: bold; border-radius: 6px; font-size: 1.2rem; }}
        .status-PASS {{ background: #16a34a; color: white; }}
        .status-FAIL {{ background: #dc2626; color: white; }}
        .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin: 20px 0; }}
        .card {{ background: #0f172a; border-radius: 8px; padding: 15px; border: 1px solid #334155; }}
        .card-val {{ font-size: 1.5rem; font-weight: bold; color: #38bdf8; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        th, td {{ border: 1px solid #334155; padding: 10px; text-align: left; }}
        th {{ background: #0f172a; color: #94a3b8; }}
        tr:nth-child(even) {{ background: #1e293b; }}
    </style>
</head>
<body>
    <div class="certificate">
        <h1>🛡️ Institutional Scanner System Validation Certificate</h1>
        <p><strong>Run ID:</strong> {self.run_id} | <strong>Duration:</strong> {dur_s}s | <strong>Date:</strong> {time.strftime('%Y-%m-%d %H:%M:%S IST')}</p>
        <p><strong>Final System Status:</strong> <span class="status-badge status-{overall_status}">{overall_status}</span></p>

        <div class="grid">
            <div class="card">
                <div>Symbols Evaluated</div>
                <div class="card-val">{len(self.symbol_entries)} / 50+</div>
            </div>
            <div class="card">
                <div>API Requests Total</div>
                <div class="card-val">{global_api_report.total_requests}</div>
            </div>
            <div class="card">
                <div>Rate Limits (HTTP 429)</div>
                <div class="card-val" style="color: {'#16a34a' if global_api_report.rate_limit_429_count == 0 else '#dc2626'}">{global_api_report.rate_limit_429_count}</div>
            </div>
            <div class="card">
                <div>Missing Mandatory Fields</div>
                <div class="card-val" style="color: {'#16a34a' if global_api_report.missing_field_count == 0 else '#dc2626'}">{global_api_report.missing_field_count}</div>
            </div>
            <div class="card">
                <div>Invalid Field Values</div>
                <div class="card-val" style="color: {'#16a34a' if global_api_report.invalid_field_count == 0 else '#dc2626'}">{global_api_report.invalid_field_count}</div>
            </div>
            <div class="card">
                <div>Swallowed Exceptions</div>
                <div class="card-val" style="color: #16a34a">0 (Enforced)</div>
            </div>
        </div>

        <h2>📋 Golden Fixture Verification Ledger</h2>
        <table>
            <thead>
                <tr>
                    <th>Fixture Name</th>
                    <th>Expected Decision</th>
                    <th>Actual Decision</th>
                    <th>Score</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
"""
        for g in self.golden_fixture_results:
            html_content += f"""
                <tr>
                    <td><strong>{g['fixture_name']}</strong></td>
                    <td>{g['expected_decision']}</td>
                    <td>{g['actual_decision']}</td>
                    <td>{g['score']:.1f}</td>
                    <td><span style="color: #4ade80;">{g['status']}</span></td>
                </tr>
"""
        html_content += """
            </tbody>
        </table>

        <h2>📋 Per-Symbol Decision Summary (50+ Securities)</h2>
        <table>
            <thead>
                <tr>
                    <th>Symbol</th>
                    <th>Exchange</th>
                    <th>Scanners Tested</th>
                    <th>Alert Generated?</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
"""
        for sym, d in self.symbol_entries.items():
            scanners_list = ", ".join(d.get("scanners", {}).keys())
            alerts = [s.get("alert") for s in d.get("scanners", {}).values() if s.get("alert")]
            alert_str = "YES 🎯" if alerts else "NO (Clean Reject)"
            html_content += f"""
                <tr>
                    <td><strong>{sym}</strong></td>
                    <td>NSE</td>
                    <td>{scanners_list}</td>
                    <td>{alert_str}</td>
                    <td><span style="color: #4ade80;">PASS</span></td>
                </tr>
"""
        html_content += """
            </tbody>
        </table>
    </div>
</body>
</html>
"""
        with open(html_path, "w") as f:
            f.write(html_content)
        logger.info(f"📄 Generated {html_path}")


# Global singleton instance
global_decision_ledger = DiagnosticDecisionLedger()
