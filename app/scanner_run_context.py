# =====================================================================================
# app/scanner_run_context.py
# [VERSION: SCANNER_RUN_CONTEXT_v1.0]
# Thread-safe telemetry container tracking scanner run metrics, freshness, and errors.
# =====================================================================================

import time
import uuid
import threading
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

STALE_THRESHOLDS = {
    "WEALTH_ENGINE": 0.40,
    "EOD": 0.25,
    "REVERSAL": 0.20,
    "PULLBACK": 0.15,
    "MULTI_TF": 0.30,
    "MULTIBAGGER": 0.25,
    "DAILY_BUILDER": 0.10,
    "PLEDGE_WORKER": 0.30,
    "AI_WORKER": 0.30,
}

class ScannerRunContext:
    def __init__(
        self,
        scanner_name: str,
        run_id: Optional[str] = None,
        trigger_type: str = "SCHEDULED",
        scheduler_name: str = "CRON",
        parent_run_id: Optional[str] = None,
        retry_attempt: int = 0,
        total_stocks: int = 0,
        system_version: Optional[str] = None,
        git_commit: Optional[str] = None,
    ):
        self.scanner_name = scanner_name
        self.run_id = run_id or uuid.uuid4().hex
        self.parent_run_id = parent_run_id
        self.retry_attempt = retry_attempt
        self.trigger_type = trigger_type
        self.scheduler_name = scheduler_name
        try:
            import config
        except ImportError:
            from . import config
        self.system_version = system_version or getattr(config, "get_system_version", lambda: getattr(config, "SYSTEM_DEPLOYMENT_VERSION", "v1"))()
        self.git_commit = git_commit or self._detect_git_commit()
        
        self.total_stocks = total_stocks
        self.fresh_count = 0
        self.stale_count = 0
        self.incomplete_count = 0
        self.alerts_generated = 0
        
        self.api_calls = 0
        self.cache_hits = 0
        self.cache_misses = 0
        
        self.stop_reason: Optional[str] = None
        self.error_summary: Optional[str] = None
        self.error_details: Optional[str] = None
        
        self.start_time = time.time()
        self.last_heartbeat = time.time()
        self._lock = threading.RLock()

    def _detect_git_commit(self) -> str:
        # Check common CI/CD and PaaS environment variables (Coolify, Railway, Render, etc.)
        import os
        for env_var in ["COMMIT_SHA", "GIT_COMMIT_SHA", "COOLIFY_GIT_COMMIT_SHA", "SOURCE_COMMIT", "RAILWAY_GIT_COMMIT_SHA", "RENDER_GIT_COMMIT"]:
            if os.environ.get(env_var):
                return os.environ[env_var][:7]

        # Check local version.json first
        try:
            import json, os
            import config
            ver_file = os.path.join(getattr(config, "BASE_DIR", "."), "app", "version.json")
            if os.path.exists(ver_file):
                with open(ver_file, "r") as f:
                    data = json.load(f)
                    if data.get("commit"):
                        return data["commit"][:7]
        except Exception:
            pass

        try:
            import subprocess
            res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout:
                return res.stdout.strip()
        except Exception:
            pass
        return "unknown"

    def heartbeat(self, force: bool = False):
        """Pulse heartbeat to database if > 15s elapsed since last update."""
        now = time.time()
        if force or (now - self.last_heartbeat >= 15.0):
            self.last_heartbeat = now
            try:
                from database import update_scanner_run_heartbeat
                update_scanner_run_heartbeat(self.run_id)
            except Exception:
                pass

    def mark_fresh(self, count: int = 1):
        with self._lock:
            self.fresh_count += count
            self.heartbeat()

    def mark_stale(self, count: int = 1):
        with self._lock:
            self.stale_count += count
            self.heartbeat()

    def mark_incomplete(self, count: int = 1):
        with self._lock:
            self.incomplete_count += count
            self.heartbeat()

    def add_alert(self, count: int = 1):
        with self._lock:
            self.alerts_generated += count
            self.heartbeat()

    def record_api_call(self, count: int = 1):
        with self._lock:
            self.api_calls += count

    def record_cache_hit(self, count: int = 1):
        with self._lock:
            self.cache_hits += count

    def record_cache_miss(self, count: int = 1):
        with self._lock:
            self.cache_misses += count

    def set_total_stocks(self, total: int):
        with self._lock:
            self.total_stocks = total

    def record_error(self, summary: str, details: Optional[str] = None):
        with self._lock:
            self.error_summary = str(summary)[:255]
            if details:
                self.error_details = str(details)

    def set_stop_reason(self, reason: str):
        with self._lock:
            self.stop_reason = str(reason)[:255]

    def compute_stale_ratio(self) -> float:
        with self._lock:
            if self.total_stocks <= 0:
                return 0.0
            return round(self.stale_count / max(1, self.total_stocks), 4)

    def evaluate_quality_status(self) -> str:
        with self._lock:
            stale_ratio = self.compute_stale_ratio()
            threshold = STALE_THRESHOLDS.get(self.scanner_name.upper(), 0.25)
            if stale_ratio > threshold:
                return "DEGRADED"
            elif self.incomplete_count > 0 or self.error_summary:
                return "PARTIAL"
            return "NORMAL"

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "run_id": self.run_id,
                "parent_run_id": self.parent_run_id,
                "retry_attempt": self.retry_attempt,
                "scanner_name": self.scanner_name,
                "trigger_type": self.trigger_type,
                "scheduler_name": self.scheduler_name,
                "system_version": self.system_version,
                "git_commit": self.git_commit,
                "total_stocks": self.total_stocks,
                "fresh_data_count": self.fresh_count,
                "stale_data_count": self.stale_count,
                "incomplete_data_count": self.incomplete_count,
                "stale_ratio": self.compute_stale_ratio(),
                "alerts_generated": self.alerts_generated,
                "api_calls": self.api_calls,
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "stop_reason": self.stop_reason,
                "error_summary": self.error_summary,
                "error_details": self.error_details,
                "quality_status": self.evaluate_quality_status(),
                "duration_seconds": round(time.time() - self.start_time, 2),
            }
