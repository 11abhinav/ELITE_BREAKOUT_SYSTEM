import time
import logging
import psutil
import os
import threading
from datetime import datetime, date
from collections import defaultdict
from typing import Dict, Any

logger = logging.getLogger("telemetry")

class CacheStats:
    def __init__(self):
        self.hits = 0
        self.misses = 0
        self.entries = 0
        self.size_mb = 0.0
        self.oldest_entry = None
        self.newest_entry = None
        self.reuse_count = 0
        self.evictions = 0
        self.total_lifetime_seconds = 0.0
        self._lock = threading.Lock()
        
    def record_hit(self):
        with self._lock:
            self.hits += 1
            self.reuse_count += 1
            
    def record_miss(self):
        with self._lock:
            self.misses += 1
            
    def record_eviction(self):
        with self._lock:
            self.evictions += 1

class NetworkStats:
    def __init__(self):
        self.api_calls = 0
        self.downloaded_bytes = 0
        self.total_download_time_sec = 0.0
        self.retries = 0
        self.rate_limit_429 = 0
        self.forbidden_403 = 0
        self.timeouts = 0
        self.failures = 0
        self._lock = threading.Lock()
        
    def record_call(self, bytes_transferred: int, duration_sec: float, status_code: int = 200, retries: int = 0, is_timeout: bool = False):
        with self._lock:
            self.api_calls += 1
            self.downloaded_bytes += bytes_transferred
            self.total_download_time_sec += duration_sec
            self.retries += retries
            
            if is_timeout:
                self.timeouts += 1
                self.failures += 1
            elif status_code == 429:
                self.rate_limit_429 += 1
                self.failures += 1
            elif status_code == 403:
                self.forbidden_403 += 1
                self.failures += 1
            elif status_code >= 400:
                self.failures += 1

class ScannerTimer:
    def __init__(self, scanner_name: str):
        self.scanner_name = scanner_name
        self.stages: Dict[str, float] = {}
        self._start_times: Dict[str, float] = {}
        
    def start_stage(self, stage_name: str):
        self._start_times[stage_name] = time.monotonic()
        
    def end_stage(self, stage_name: str):
        if stage_name in self._start_times:
            duration = time.monotonic() - self._start_times.pop(stage_name)
            self.stages[stage_name] = self.stages.get(stage_name, 0.0) + duration
            
    def total_time(self) -> float:
        return sum(self.stages.values())
        
    def report(self) -> str:
        rep = f"Scanner Timing: {self.scanner_name}\n"
        for stage, dur in self.stages.items():
            rep += f"  - {stage}: {dur:.2f}s\n"
        rep += f"  - TOTAL: {self.total_time():.2f}s\n"
        return rep


class TelemetryManager:
    """
    Singleton manager tracking observability metrics across the system.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(TelemetryManager, cls).__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self):
        self.cache_stats = CacheStats()
        self.network_stats = NetworkStats()
        self._timers: Dict[str, ScannerTimer] = {}
        
    def get_timer(self, scanner_name: str) -> ScannerTimer:
        if scanner_name not in self._timers:
            self._timers[scanner_name] = ScannerTimer(scanner_name)
        return self._timers[scanner_name]
        
    def log_memory_snapshot(self, context: str = "SNAPSHOT"):
        process = psutil.Process(os.getpid())
        rss_mb = process.memory_info().rss / (1024 * 1024)
        vms_mb = process.memory_info().vms / (1024 * 1024)
        
        hit_rate = 0.0
        total_cache_reqs = self.cache_stats.hits + self.cache_stats.misses
        if total_cache_reqs > 0:
            hit_rate = (self.cache_stats.hits / total_cache_reqs) * 100
            
        logger.info(f"========== {context} MEMORY SNAPSHOT ==========")
        logger.info(f"Time: {datetime.now().strftime('%H:%M:%S')}")
        logger.info(f"Process RSS memory: {rss_mb:.1f} MB")
        logger.info(f"Process VMS memory: {vms_mb:.1f} MB")
        logger.info(f"Total cached symbols: {self.cache_stats.entries}")
        logger.info(f"Historical cache size: {len(self._timers)} Symbols")
        logger.info(f"Indicator cache size: {self.cache_stats.entries} Objects")
        logger.info(f"Number of active scanner caches: {len(self._timers)}")
        logger.info(f"Cache hit rate: {hit_rate:.1f}% ({self.cache_stats.hits} hits)")
        logger.info(f"Cache miss rate: {100.0 - hit_rate:.1f}% ({self.cache_stats.misses} misses)")
        logger.info("=============================================")

# Global singleton access
telemetry = TelemetryManager()
