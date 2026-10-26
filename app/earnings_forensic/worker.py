"""
app/earnings_forensic/worker.py
Async forensic background processor with scan lock and daily AI cost guard.

Features:
  - Consumes filings marked 'FORENSIC_PENDING' or processes targeted symbol/period
  - Strictly deterministic-first: fact sheet normalization + multi-rule engine
  - Optional AI enrichment guarded by daily limit (default 50 calls/day)
  - Updates corporate_filings_log status to FORENSIC_COMPLETE or FORENSIC_FAILED
  - Registers scanner telemetry & health under "Earnings Forensic"
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.database import (
    get_connection,
    start_scanner_execution_run,
    complete_scanner_execution_run,
    upsert_scanner_health,
    is_scanner_stopped,
)
from .schema import (
    ForensicReport,
    ReportStatus,
    FORENSIC_ENGINE_VERSION,
)
from .normalizer import build_factsheet
from .engine import run_all_checks
from .ai_analyzer import analyze
from .repository import (
    init_earnings_forensic_db,
    get_pending_filings,
    mark_filing_status,
    upsert_forensic_report,
)

logger = logging.getLogger(__name__)

# Daily AI call limit to guard API expenses
MAX_FORENSIC_AI_CALLS_PER_DAY = int(os.environ.get("MAX_FORENSIC_AI_CALLS_PER_DAY", "50"))
SCANNER_NAME = "Earnings Forensic"


def _get_today_ai_calls_count() -> int:
    """Counts how many reports were enriched with an AI model today."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            sql = """
                SELECT COUNT(*) FROM earnings_forensic_reports
                WHERE ai_model_used IS NOT NULL
                  AND created_at >= CURRENT_DATE
            """
            cur.execute(sql)
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception as e:
        logger.warning(f"[EARNINGS_FORENSIC] Error checking daily AI call budget: {e}")
        return 0


def process_single_filing(
    symbol: str,
    period_end: str,
    basis: str = "CONSOLIDATED",
    statement_type: str = "QUARTERLY",
    filing_id: Optional[str] = None,
    source_hash: Optional[str] = None,
    enable_ai: bool = True,
    ai_budget_remaining: int = 10,
) -> Optional[ForensicReport]:
    """
    Executes the end-to-end pipeline for a single filing:
      1. Normalize PIT raw filings into ForensicFactSheet
      2. Run all deterministic checks
      3. Conditionally enrich with AI if budget permits
      4. Persist result into database
    """
    logger.info(f"[EARNINGS_FORENSIC] Processing {symbol} for period {period_end} ({basis})...")

    # 1. Build fact sheet
    factsheet = build_factsheet(
        symbol=symbol,
        period_end=period_end,
        preferred_basis=basis,
        filing_id=filing_id,
        source_hash=source_hash,
    )
    if not factsheet or not factsheet.current:
        logger.warning(f"[EARNINGS_FORENSIC] No PIT data found for {symbol} on {period_end}")
        return None

    # 2. Run deterministic engine
    report = run_all_checks(factsheet)
    report.statement_type = statement_type
    report.reporting_basis = basis
    report.filing_id = filing_id
    report.source_hash = source_hash

    # 3. AI Enrichment (if allowed and budget permits)
    if enable_ai and ai_budget_remaining > 0:
        try:
            report = analyze(report)
        except Exception as e:
            logger.warning(f"[EARNINGS_FORENSIC] AI enrichment failed for {symbol}: {e}")
    else:
        logger.info(f"[EARNINGS_FORENSIC] Skipping AI enrichment for {symbol} (Budget remaining: {ai_budget_remaining})")

    report.status = ReportStatus.COMPLETE

    # 4. Upsert report
    success = upsert_forensic_report(report)
    if not success:
        report.status = ReportStatus.FAILED

    return report


def run_forensic_worker_scan_once(limit: int = 20, enable_ai: bool = True) -> Dict[str, Any]:
    """
    Worker invocation called by master orchestrator or CLI.
    Processes queued 'FORENSIC_PENDING' filings or checks newly ingested filings.
    """
    if is_scanner_stopped(SCANNER_NAME):
        logger.info(f"[{SCANNER_NAME}] Scanner is marked STOPPED. Skipping execution.")
        return {"status": "STOPPED", "processed": 0}

    init_earnings_forensic_db()
    run_id = start_scanner_execution_run(SCANNER_NAME)
    logger.info(f"[{SCANNER_NAME}] Starting execution run: {run_id}")

    processed_count = 0
    success_count = 0
    failed_count = 0
    start_time = time.time()

    try:
        pending_items = get_pending_filings(limit=limit)
        logger.info(f"[{SCANNER_NAME}] Found {len(pending_items)} pending filings.")

        today_ai_calls = _get_today_ai_calls_count()
        ai_budget_left = max(0, MAX_FORENSIC_AI_CALLS_PER_DAY - today_ai_calls)

        for item in pending_items:
            filing_db_id = item["id"]
            symbol = item["symbol"]
            period_end = item["period_end"]
            basis = item["basis"] or "CONSOLIDATED"
            st_type = item["statement_type"] or "QUARTERLY"
            filing_id = item["filing_id"]
            raw_facts = item.get("raw_facts", {})
            src_hash = raw_facts.get("source_hash")

            try:
                rep = process_single_filing(
                    symbol=symbol,
                    period_end=period_end,
                    basis=basis,
                    statement_type=st_type,
                    filing_id=filing_id,
                    source_hash=src_hash,
                    enable_ai=enable_ai,
                    ai_budget_remaining=ai_budget_left,
                )
                if rep and rep.status == ReportStatus.COMPLETE:
                    mark_filing_status(filing_db_id, "FORENSIC_COMPLETE")
                    success_count += 1
                    if rep.ai_model_used:
                        ai_budget_left = max(0, ai_budget_left - 1)
                else:
                    mark_filing_status(filing_db_id, "FORENSIC_FAILED")
                    failed_count += 1
            except Exception as e:
                logger.error(f"[{SCANNER_NAME}] Failed to process {symbol}: {e}", exc_info=True)
                mark_filing_status(filing_db_id, "FORENSIC_FAILED")
                failed_count += 1

            processed_count += 1

        duration = time.time() - start_time
        complete_scanner_execution_run(
            run_id=run_id,
            scanned_count=processed_count,
            candidates_found=success_count,
            error_message=None if failed_count == 0 else f"{failed_count} filings failed processing",
        )
        health_status = "OK" if failed_count <= max(1, processed_count * 0.1) else "DEGRADED"
        upsert_scanner_health(
            scanner_name=SCANNER_NAME,
            status=health_status,
            latency_ms=int(duration * 1000),
            error_message=f"Success: {success_count}, Failed: {failed_count}" if failed_count > 0 else None,
        )

        return {
            "status": "COMPLETED",
            "run_id": run_id,
            "processed": processed_count,
            "success": success_count,
            "failed": failed_count,
            "duration_sec": round(duration, 2),
        }

    except Exception as exc:
        duration = time.time() - start_time
        logger.error(f"[{SCANNER_NAME}] Worker failed with exception: {exc}", exc_info=True)
        complete_scanner_execution_run(
            run_id=run_id,
            scanned_count=processed_count,
            candidates_found=success_count,
            error_message=str(exc),
        )
        upsert_scanner_health(
            scanner_name=SCANNER_NAME,
            status="DOWN",
            latency_ms=int(duration * 1000),
            error_message=str(exc),
        )
        return {"status": "ERROR", "error": str(exc)}
