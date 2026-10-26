"""
app/earnings_forensic/repository.py
Database access layer for the Earnings Forensic & Hidden Facts Scanner.

Rules:
  - All DB operations use get_connection() context from app.database
  - Uses native PostgreSQL JSONB and parameterized queries
  - Upsert is idempotent on (symbol, period_end, statement_type, reporting_basis, report_version)
  - Updates corporate_filings_log status safely
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.database import get_connection
from .schema import (
    EarningsGrade,
    ForensicFinding,
    ForensicReport,
    ReportStatus,
    RevenueQuality,
    ProfitQuality,
    CashConversionQuality,
)

logger = logging.getLogger(__name__)


def init_earnings_forensic_db() -> None:
    """Ensures earnings_forensic_reports table and indexes exist."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS earnings_forensic_reports (
                    id BIGSERIAL PRIMARY KEY,
                    symbol VARCHAR(30) NOT NULL,
                    period_end VARCHAR(20) NOT NULL,
                    statement_type VARCHAR(30) NOT NULL DEFAULT 'QUARTERLY',
                    reporting_basis VARCHAR(30) NOT NULL DEFAULT 'CONSOLIDATED',
                    filing_id VARCHAR(128),
                    report_version INTEGER NOT NULL DEFAULT 1,
                    source_hash VARCHAR(64),
                    engine_version VARCHAR(20) NOT NULL,
                    headline_verdict TEXT NOT NULL,
                    earnings_grade VARCHAR(5) NOT NULL DEFAULT 'U',
                    revenue_quality VARCHAR(30) NOT NULL,
                    profit_quality VARCHAR(30) NOT NULL,
                    cash_conversion VARCHAR(30) NOT NULL,
                    revenue_yoy_pct NUMERIC(10, 2),
                    revenue_qoq_pct NUMERIC(10, 2),
                    op_profit_yoy_pct NUMERIC(10, 2),
                    net_profit_yoy_pct NUMERIC(10, 2),
                    opm_current NUMERIC(6, 2),
                    opm_yoy_delta_bps NUMERIC(10, 2),
                    npm_current NUMERIC(6, 2),
                    cfo_pat_ratio NUMERIC(10, 2),
                    cfo_pat_3y_avg NUMERIC(10, 2),
                    ttm_revenue NUMERIC(16, 2),
                    ttm_net_profit NUMERIC(16, 2),
                    evidence_coverage_pct NUMERIC(5, 2) NOT NULL DEFAULT 0.0,
                    findings JSONB NOT NULL DEFAULT '[]'::jsonb,
                    ai_findings JSONB NOT NULL DEFAULT '[]'::jsonb,
                    investor_questions JSONB NOT NULL DEFAULT '[]'::jsonb,
                    evidence_missing JSONB NOT NULL DEFAULT '[]'::jsonb,
                    raw_report_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    status VARCHAR(30) NOT NULL DEFAULT 'COMPLETE',
                    ai_model_used VARCHAR(50),
                    error_message TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT efr_sym_period_basis_v_uniq UNIQUE (symbol, period_end, statement_type, reporting_basis, report_version)
                );
                CREATE INDEX IF NOT EXISTS idx_efr_symbol_period ON earnings_forensic_reports(symbol, period_end DESC);
                CREATE INDEX IF NOT EXISTS idx_efr_created_at ON earnings_forensic_reports(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_efr_grade ON earnings_forensic_reports(earnings_grade);
                CREATE INDEX IF NOT EXISTS idx_efr_status ON earnings_forensic_reports(status);
                CREATE INDEX IF NOT EXISTS idx_efr_source_hash ON earnings_forensic_reports(source_hash);
            """)
            conn.commit()
    except Exception as e:
        logger.warning(f"[EARNINGS_FORENSIC] DB init notice: {e}")


def get_pending_filings(limit: int = 10) -> List[Dict[str, Any]]:
    """
    Fetches corporate_filings_log records with status = 'FORENSIC_PENDING'
    or newly discovered UNSEEN_PERIOD / NEW_FILING records.
    """
    results = []
    try:
        with get_connection() as conn, conn.cursor() as cur:
            query = """
                SELECT id, symbol, filing_id, period_end, statement_type, basis,
                       event_type, filing_date, raw_facts, status
                FROM corporate_filings_log
                WHERE status = 'FORENSIC_PENDING'
                ORDER BY discovered_at ASC
                LIMIT %s
            """
            cur.execute(query, (limit,))
            rows = cur.fetchall()
            for r in rows:
                raw_f = r[8]
                if isinstance(raw_f, str):
                    try:
                        raw_f = json.loads(raw_f)
                    except Exception:
                        raw_f = {}
                results.append({
                    "id": r[0],
                    "symbol": str(r[1]).upper(),
                    "filing_id": r[2],
                    "period_end": r[3],
                    "statement_type": r[4] or "QUARTERLY",
                    "basis": r[5] or "CONSOLIDATED",
                    "event_type": r[6],
                    "filing_date": str(r[7]),
                    "raw_facts": raw_f or {},
                    "status": r[9],
                })
    except Exception as e:
        logger.error(f"[EARNINGS_FORENSIC] Error fetching pending filings: {e}", exc_info=True)
    return results


def mark_filing_status(filing_log_id: int, status: str) -> None:
    """Updates status in corporate_filings_log (e.g. FORENSIC_COMPLETE, FORENSIC_FAILED)."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            sql = "UPDATE corporate_filings_log SET status = %s, updated_at = NOW() WHERE id = %s"
            cur.execute(sql, (status, filing_log_id))
            conn.commit()
    except Exception as e:
        logger.error(f"[EARNINGS_FORENSIC] Failed to update filing status: {e}")


def get_latest_report(symbol: str, period_end: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Returns the latest completed report for symbol (optionally for specific period)."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            if period_end:
                sql = """
                    SELECT raw_report_json FROM earnings_forensic_reports
                    WHERE symbol = %s AND period_end = %s
                    ORDER BY report_version DESC, created_at DESC
                    LIMIT 1
                """
                cur.execute(sql, (symbol.upper(), period_end))
            else:
                sql = """
                    SELECT raw_report_json FROM earnings_forensic_reports
                    WHERE symbol = %s
                    ORDER BY period_end DESC, report_version DESC, created_at DESC
                    LIMIT 1
                """
                cur.execute(sql, (symbol.upper(),))
            row = cur.fetchone()
            if row and row[0]:
                return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    except Exception as e:
        logger.error(f"[EARNINGS_FORENSIC] Error getting latest report for {symbol}: {e}")
    return None


def get_recent_reports(limit: int = 50, status: str = "COMPLETE") -> List[Dict[str, Any]]:
    """Returns recent reports for the dashboard overview."""
    results = []
    try:
        with get_connection() as conn, conn.cursor() as cur:
            sql = """
                SELECT symbol, period_end, statement_type, reporting_basis,
                       headline_verdict, earnings_grade, revenue_quality, profit_quality,
                       cash_conversion, revenue_yoy_pct, net_profit_yoy_pct, opm_current,
                       cfo_pat_ratio, evidence_coverage_pct, ai_model_used, status,
                       created_at, raw_report_json
                FROM earnings_forensic_reports
                WHERE (%s = 'ALL' OR status = %s)
                ORDER BY created_at DESC
                LIMIT %s
            """
            cur.execute(sql, (status, status, limit))
            rows = cur.fetchall()
            for r in rows:
                raw_json = r[17]
                if isinstance(raw_json, str):
                    try:
                        raw_json = json.loads(raw_json)
                    except Exception:
                        raw_json = {}
                results.append({
                    "symbol": r[0],
                    "period_end": r[1],
                    "statement_type": r[2],
                    "reporting_basis": r[3],
                    "headline_verdict": r[4],
                    "earnings_grade": r[5],
                    "revenue_quality": r[6],
                    "profit_quality": r[7],
                    "cash_conversion": r[8],
                    "revenue_yoy_pct": float(r[9]) if r[9] is not None else None,
                    "net_profit_yoy_pct": float(r[10]) if r[10] is not None else None,
                    "opm_current": float(r[11]) if r[11] is not None else None,
                    "cfo_pat_ratio": float(r[12]) if r[12] is not None else None,
                    "evidence_coverage_pct": float(r[13]) if r[13] is not None else 0.0,
                    "ai_model_used": r[14],
                    "status": r[15],
                    "created_at": str(r[16]),
                    "raw_report": raw_json or {},
                })
    except Exception as e:
        logger.error(f"[EARNINGS_FORENSIC] Error fetching recent reports: {e}")
    return results


def upsert_forensic_report(report: ForensicReport) -> bool:
    """
    Persists a completed ForensicReport into earnings_forensic_reports.
    Performs conflict resolution on (symbol, period_end, statement_type, reporting_basis, report_version).
    """
    init_earnings_forensic_db()
    rep_dict = report.to_dict()
    findings_json = json.dumps([f.to_dict() if hasattr(f, "to_dict") else f.__dict__ for f in report.findings], default=str)
    ai_findings_json = json.dumps([f.to_dict() if hasattr(f, "to_dict") else f.__dict__ for f in report.ai_findings], default=str)
    questions_json = json.dumps(report.investor_questions, default=str)
    missing_json = json.dumps(report.evidence_missing, default=str)
    raw_json = json.dumps(rep_dict, default=str)

    grade_val = report.earnings_grade.value if hasattr(report.earnings_grade, "value") else str(report.earnings_grade)
    rev_q = report.revenue_quality.value if hasattr(report.revenue_quality, "value") else str(report.revenue_quality)
    prof_q = report.profit_quality.value if hasattr(report.profit_quality, "value") else str(report.profit_quality)
    cash_q = report.cash_conversion.value if hasattr(report.cash_conversion, "value") else str(report.cash_conversion)
    stat_val = report.status.value if hasattr(report.status, "value") else str(report.status)

    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO earnings_forensic_reports (
                    symbol, period_end, statement_type, reporting_basis, filing_id,
                    report_version, source_hash, engine_version, headline_verdict,
                    earnings_grade, revenue_quality, profit_quality, cash_conversion,
                    revenue_yoy_pct, revenue_qoq_pct, op_profit_yoy_pct, net_profit_yoy_pct,
                    opm_current, opm_yoy_delta_bps, npm_current, cfo_pat_ratio,
                    cfo_pat_3y_avg, ttm_revenue, ttm_net_profit, evidence_coverage_pct,
                    findings, ai_findings, investor_questions, evidence_missing,
                    raw_report_json, status, ai_model_used, error_message, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s::jsonb, %s, %s, %s, NOW()
                )
                ON CONFLICT (symbol, period_end, statement_type, reporting_basis, report_version)
                DO UPDATE SET
                    source_hash = EXCLUDED.source_hash,
                    headline_verdict = EXCLUDED.headline_verdict,
                    earnings_grade = EXCLUDED.earnings_grade,
                    revenue_quality = EXCLUDED.revenue_quality,
                    profit_quality = EXCLUDED.profit_quality,
                    cash_conversion = EXCLUDED.cash_conversion,
                    revenue_yoy_pct = EXCLUDED.revenue_yoy_pct,
                    revenue_qoq_pct = EXCLUDED.revenue_qoq_pct,
                    op_profit_yoy_pct = EXCLUDED.op_profit_yoy_pct,
                    net_profit_yoy_pct = EXCLUDED.net_profit_yoy_pct,
                    opm_current = EXCLUDED.opm_current,
                    opm_yoy_delta_bps = EXCLUDED.opm_yoy_delta_bps,
                    npm_current = EXCLUDED.npm_current,
                    cfo_pat_ratio = EXCLUDED.cfo_pat_ratio,
                    cfo_pat_3y_avg = EXCLUDED.cfo_pat_3y_avg,
                    ttm_revenue = EXCLUDED.ttm_revenue,
                    ttm_net_profit = EXCLUDED.ttm_net_profit,
                    evidence_coverage_pct = EXCLUDED.evidence_coverage_pct,
                    findings = EXCLUDED.findings,
                    ai_findings = EXCLUDED.ai_findings,
                    investor_questions = EXCLUDED.investor_questions,
                    evidence_missing = EXCLUDED.evidence_missing,
                    raw_report_json = EXCLUDED.raw_report_json,
                    status = EXCLUDED.status,
                    ai_model_used = EXCLUDED.ai_model_used,
                    error_message = EXCLUDED.error_message,
                    updated_at = NOW()
            """, (
                report.symbol.upper(), report.period_end, report.statement_type, report.reporting_basis, report.filing_id,
                report.report_version, report.source_hash, report.engine_version, report.headline_verdict,
                grade_val, rev_q, prof_q, cash_q,
                report.revenue_yoy_pct, report.revenue_qoq_pct, report.op_profit_yoy_pct, report.net_profit_yoy_pct,
                report.opm_current, report.opm_yoy_delta_bps, report.npm_current, report.cfo_pat_ratio,
                report.cfo_pat_3y_avg, report.ttm_revenue, report.ttm_net_profit, report.evidence_coverage_pct,
                findings_json, ai_findings_json, questions_json, missing_json,
                raw_json, stat_val, report.ai_model_used, report.error_message
            ))
            conn.commit()
            logger.info(f"[EARNINGS_FORENSIC] Persisted report for {report.symbol} {report.period_end} (Grade: {grade_val})")
            return True
    except Exception as e:
        logger.error(f"[EARNINGS_FORENSIC] Failed to persist report for {report.symbol}: {e}", exc_info=True)
        return False
