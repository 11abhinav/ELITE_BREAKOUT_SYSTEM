# =====================================================================================
# app/scanner_contract.py — INSTITUTIONAL SCANNER EXECUTION CONTRACT & HEALTH ACCOUNTING
# =====================================================================================
import time
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

@dataclass
class ScannerResult:
    scanner_name: str
    success: bool
    status: str                         # "OK" | "DOWN" | "DEGRADED"
    total_symbols: int
    processed_symbols: int
    missing_symbols: List[str]
    failed_providers: List[str]
    error_msg: Optional[str]
    duration_seconds: float
    phases: Dict[str, Dict[str, Any]] = field(default_factory=dict)

class ScannerExecutionContract:
    """
    Institutional Scanner Execution Accounting & Health Life-Cycle Manager.
    
    INVARIANT:
    - Provider / API health (Fyers/Upstox/Yahoo) tracks network layer availability.
    - Scanner health (OK vs DOWN) tracks whether the scanner satisfied its required data contract.
    - A scanner CANNOT be marked OK unless all mandatory symbols/phases complete successfully.
    - Missing or invalid required data marks the scanner as DOWN with detailed failure traces.
    """
    def __init__(self, scanner_name: str, total_symbols: int = 0):
        from database import normalize_scanner_name, upsert_scanner_health
        self.scanner_name = normalize_scanner_name(scanner_name)
        self.total_symbols = total_symbols
        self.start_time = time.monotonic()
        self.start_ts = datetime.now(IST).isoformat()
        self.missing_symbols: List[str] = []
        self.failed_providers: List[str] = []
        self.phases: Dict[str, Dict[str, Any]] = {}
        
        # Immediately set status to RUNNING on start
        try:
            upsert_scanner_health(
                scanner_name=self.scanner_name,
                status="RUNNING",
                error_msg=f"Scan execution in progress...",
                total_count=total_symbols
            )
        except Exception as e:
            logger.warning(f"Failed to set scanner RUNNING status for {self.scanner_name}: {e}")

    def mark_phase(self, phase_name: str, status: str, details: str = "") -> None:
        """
        Record lifecycle phase status: 'NOT_STARTED', 'RUNNING', 'SUCCESS', 'DEGRADED', 'FAILED'.
        """
        self.phases[phase_name] = {
            "status": status,
            "details": details,
            "timestamp": datetime.now(IST).strftime("%H:%M:%S")
        }
        logger.info(f"📋 [{self.scanner_name}] Phase '{phase_name}': {status} ({details})")

    def complete(
        self,
        missing_symbols: Optional[List[str]] = None,
        error: Optional[Exception] = None,
        processed_count: Optional[int] = None
    ) -> ScannerResult:
        """
        Evaluates the final execution contract and updates scanner_health DB table.
        """
        from database import upsert_scanner_health, get_connection
        duration = round(time.monotonic() - self.start_time, 2)
        missing = missing_symbols or []
        
        if error:
            success = False
            status = "DOWN"
            err_text = f"Unhandled Exception: {str(error)[:400]}"
        elif missing:
            success = False
            status = "DOWN"
            sym_list_str = ", ".join(missing[:10])
            if len(missing) > 10:
                sym_list_str += f" (+{len(missing)-10} more)"
            err_text = f"Required market data contract unfulfilled — {len(missing)} missing symbols: [{sym_list_str}]"
        else:
            success = True
            status = "OK"
            err_text = None

        proc_cnt = processed_count if processed_count is not None else (self.total_symbols - len(missing))

        res = ScannerResult(
            scanner_name=self.scanner_name,
            success=success,
            status=status,
            total_symbols=self.total_symbols,
            processed_symbols=proc_cnt,
            missing_symbols=missing,
            failed_providers=self.failed_providers,
            error_msg=err_text,
            duration_seconds=duration,
            phases=self.phases
        )

        # Update scanner_health DB table with true execution status
        try:
            upsert_scanner_health(
                scanner_name=self.scanner_name,
                status=status,
                error_msg=err_text,
                processed_count=proc_cnt,
                total_count=self.total_symbols,
                outcome="SUCCESS" if success else "FAILED",
                duration_seconds=duration
            )
        except Exception as db_err:
            logger.warning(f"Failed to update upsert_scanner_health for {self.scanner_name}: {db_err}")

        # If scan failed, write failure record into scan_failures table for UI error trace
        if not success:
            try:
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO scan_failures (scan_id, scanner_name, symbol, provider, failure_reason, failed_at)
                            VALUES (%s, %s, %s, 'SCANNER_CONTRACT', %s, NOW());
                        """, (f"CONTRACT_{self.scanner_name}_{int(time.time())}", self.scanner_name, missing[0] if missing else 'SYSTEM', err_text))
                        conn.commit()
            except Exception as sf_err:
                logger.warning(f"Failed to record scan_failures entry: {sf_err}")

            logger.error(f"❌ [{self.scanner_name} EXECUTION CONTRACT FAILED] Status={status} | Error={err_text}")
        else:
            logger.info(f"✅ [{self.scanner_name} EXECUTION CONTRACT OK] Processed {proc_cnt}/{self.total_symbols} symbols in {duration}s")

        return res
