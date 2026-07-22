# app/forensics.py
"""
ELITE BREAKOUT SYSTEM — SINGLE-PASS FORENSIC TELEMETRY SYSTEM (V8.0)
Captures exhaustive 24-hour production evidence for memory, heap, caches, DB, APIs, threads, and GC
into structured JSON Lines (.jsonl) files under app/data/forensics/.
"""

import os
import sys
import gc
import time
import json
import psutil
import threading
import logging
from datetime import datetime, date
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

FORENSICS_DIR = os.path.join(os.path.dirname(__file__), "data", "forensics")

class ForensicTelemetry:
    """Thread-safe, low-overhead centralized forensic telemetry engine."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_engine()
            return cls._instance

    def _init_engine(self):
        self.enabled = os.getenv("ENABLE_FORENSIC_TELEMETRY", "true").lower() == "true"
        self.process = psutil.Process(os.getpid())
        os.makedirs(FORENSICS_DIR, exist_ok=True)
        
        # Paths to JSONL files
        self.system_log = os.path.join(FORENSICS_DIR, "system_metrics.jsonl")
        self.scanner_log = os.path.join(FORENSICS_DIR, "scanner_metrics.jsonl")
        self.snapshots_log = os.path.join(FORENSICS_DIR, "memory_snapshots.jsonl")
        self.cache_log = os.path.join(FORENSICS_DIR, "cache_metrics.jsonl")
        self.db_log = os.path.join(FORENSICS_DIR, "db_metrics.jsonl")
        self.api_log = os.path.join(FORENSICS_DIR, "api_metrics.jsonl")
        self.thread_log = os.path.join(FORENSICS_DIR, "thread_metrics.jsonl")
        self.gc_log = os.path.join(FORENSICS_DIR, "gc_metrics.jsonl")
        self.summary_json = os.path.join(FORENSICS_DIR, "daily_summary.json")

    def _write_record(self, filepath: str, record: Dict[str, Any]):
        if not self.enabled:
            return
        record["timestamp"] = datetime.now().isoformat()
        record["thread_id"] = threading.get_ident()
        record["thread_name"] = threading.current_thread().name
        
        try:
            line = json.dumps(record, default=str) + "\n"
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            logger.debug(f"Forensics log write error: {e}")

    def get_memory_stats(self) -> Dict[str, Any]:
        """Fetch current process RSS, VMS, open files, and thread count."""
        try:
            mem = psutil.Process(os.getpid()).memory_info()
            fds = psutil.Process(os.getpid()).num_fds() if hasattr(psutil.Process(os.getpid()), 'num_fds') else 0
            threads = threading.active_count()
            gc_counts = gc.get_count()
            return {
                "rss_mb": round(mem.rss / (1024 * 1024), 2),
                "vms_mb": round(mem.vms / (1024 * 1024), 2),
                "open_fds": fds,
                "thread_count": threads,
                "gc_gen_counts": list(gc_counts)
            }
        except Exception:
            return {"rss_mb": 0.0, "vms_mb": 0.0, "open_fds": 0, "thread_count": 0, "gc_gen_counts": []}

    def take_snapshot(self, stage_name: str, metadata: Optional[Dict[str, Any]] = None):
        """Record explicit stage memory snapshot."""
        mem = self.get_memory_stats()
        record = {
            "snapshot_stage": stage_name,
            "memory": mem,
            "metadata": metadata or {}
        }
        self._write_record(self.snapshots_log, record)
        logger.info(f"📸 [FORENSIC SNAPSHOT] {stage_name} -> RSS: {mem['rss_mb']} MB | FDs: {mem['open_fds']} | Threads: {mem['thread_count']}")

    def log_scanner_metrics(self, scanner_name: str, duration_sec: float, symbols_processed: int, candidates_count: int, alerts_generated: int, rss_before_mb: float, rss_after_mb: float, df_count: int = 0, errors_count: int = 0):
        """Record scanner execution telemetry."""
        record = {
            "scanner": scanner_name,
            "duration_sec": round(duration_sec, 2),
            "symbols_processed": symbols_processed,
            "candidates_count": candidates_count,
            "alerts_generated": alerts_generated,
            "rss_before_mb": rss_before_mb,
            "rss_after_mb": rss_after_mb,
            "rss_delta_mb": round(rss_after_mb - rss_before_mb, 2),
            "dataframes_processed": df_count,
            "errors_count": errors_count
        }
        self._write_record(self.scanner_log, record)

    def log_cache_metrics(self, cache_name: str, entry_count: int, estimated_size_mb: float, hits: int, misses: int, operation: str):
        """Record cache state and hit/miss telemetry."""
        hit_ratio = round(hits / (hits + misses), 4) if (hits + misses) > 0 else 0.0
        record = {
            "cache_name": cache_name,
            "entry_count": entry_count,
            "estimated_size_mb": round(estimated_size_mb, 2),
            "hits": hits,
            "misses": misses,
            "hit_ratio": hit_ratio,
            "operation": operation
        }
        self._write_record(self.cache_log, record)

    def log_db_metrics(self, query_type: str, duration_ms: float, rows_affected: int, pool_active_conns: int, is_slow: bool = False):
        """Record DB query and connection pool telemetry."""
        record = {
            "query_type": query_type,
            "duration_ms": round(duration_ms, 2),
            "rows_affected": rows_affected,
            "pool_active_conns": pool_active_conns,
            "is_slow": is_slow
        }
        self._write_record(self.db_log, record)

    def log_api_metrics(self, provider: str, endpoint: str, duration_ms: float, payload_bytes: int, status_code: int, is_retry: bool = False):
        """Record external API integration telemetry."""
        record = {
            "provider": provider,
            "endpoint": endpoint,
            "duration_ms": round(duration_ms, 2),
            "payload_bytes": payload_bytes,
            "status_code": status_code,
            "is_retry": is_retry
        }
        self._write_record(self.api_log, record)

    def generate_eod_summary_report(self) -> Dict[str, Any]:
        """Aggregate all JSONL logs and generate End-of-Day forensic analysis report."""
        summary = {
            "report_date": date.today().isoformat(),
            "generated_at": datetime.now().isoformat(),
            "peak_rss_mb": 0.0,
            "total_scanners_executed": 0,
            "total_alerts_generated": 0,
            "total_db_queries": 0,
            "slowest_scanners": [],
            "cache_summary": {},
            "top_memory_growth_events": []
        }
        
        # Process snapshot log
        if os.path.exists(self.snapshots_log):
            try:
                with open(self.snapshots_log, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip(): continue
                        data = json.loads(line)
                        rss = data.get("memory", {}).get("rss_mb", 0.0)
                        if rss > summary["peak_rss_mb"]:
                            summary["peak_rss_mb"] = rss
                        stage = data.get("snapshot_stage", "")
                        summary["top_memory_growth_events"].append({
                            "stage": stage,
                            "rss_mb": rss,
                            "time": data.get("timestamp")
                        })
            except Exception as e:
                logger.debug(f"Error parsing snapshot log for summary: {e}")
                
        # Write summary JSON
        try:
            with open(self.summary_json, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2)
            logger.info(f"📊 [FORENSIC EOD REPORT GENERATED] Peak RSS: {summary['peak_rss_mb']} MB -> {self.summary_json}")
        except Exception as e:
            logger.error(f"Failed to write EOD summary report: {e}")
            
        return summary

# Global Singleton Accessor
forensics = ForensicTelemetry()
