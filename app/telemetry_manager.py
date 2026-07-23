import logging
import psutil
import os
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

# Configure logger if not already configured in main app
logger = logging.getLogger("Telemetry")
if not logger.handlers:
    # Fallback if standard app logger isn't intercepting this
    ch = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    logger.setLevel(logging.INFO)


class TelemetryManager:
    """
    STRICTLY PASSIVE OBSERVABILITY SUBSYSTEM
    
    This singleton must NEVER:
    - Clear caches
    - Trigger GC
    - Decide ownership
    - Release memory
    - Influence scheduling
    
    It only records, measures, and reports system state.
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_state()
        return cls._instance
        
    def _init_state(self):
        self.daily_metrics = {
            "peak_rss": 0,
            "min_rss": float('inf'),
            "avg_rss_sum": 0,
            "rss_samples": 0,
            "largest_dataset": {"name": None, "size_mb": 0},
            "largest_temp_alloc": {"name": None, "size_mb": 0},
            "most_reused": {"name": None, "count": 0},
            "dataset_reuse_counts": {},
            "longest_fetch": {"name": None, "time": 0},
            "fastest_scanner": {"name": None, "time": float('inf')},
            "slowest_scanner": {"name": None, "time": 0},
            "cache_hits": 0,
            "cache_misses": 0,
            "duplicate_fetches": 0,
            "max_scheduler_drift": 0,
            "memory_recovered_mb": 0,
            "memory_not_recovered_mb": 0,
            "leak_candidates": set()
        }
    
    def _record_rss(self, rss_mb):
        if rss_mb > self.daily_metrics["peak_rss"]:
            self.daily_metrics["peak_rss"] = rss_mb
        if rss_mb < self.daily_metrics["min_rss"]:
            self.daily_metrics["min_rss"] = rss_mb
        self.daily_metrics["avg_rss_sum"] += rss_mb
        self.daily_metrics["rss_samples"] += 1
        
    def get_current_rss_mb(self):
        process = psutil.Process(os.getpid())
        return process.memory_info().rss / (1024 * 1024)
        
    def log_fetch(self, dataset: str, interval: str, period: str, provider: str, reason: str, cache_status: str, fetch_time: float, memory_mb: float, consumers: list, fetch_count: int, reuse_count: int):
        if cache_status.upper() == "HIT":
            self.daily_metrics["cache_hits"] += 1
        else:
            self.daily_metrics["cache_misses"] += 1
            if fetch_time > self.daily_metrics["longest_fetch"]["time"]:
                self.daily_metrics["longest_fetch"] = {"name": f"{dataset} via {provider}", "time": fetch_time}
        
        self._record_rss(memory_mb)
        
        consumers_str = ",".join(consumers) if consumers else "None"
        
        log_str = (
            f"\n[ROUTING]\n"
            f"Dataset          : {dataset}\n"
            f"Interval         : {interval}\n"
            f"Period           : {period}\n"
            f"Provider         : {provider}\n"
            f"Reason           : {reason}\n"
            f"Cache Status     : {cache_status}\n"
            f"Fetch Time       : {fetch_time:.2f} sec\n"
            f"Memory           : {memory_mb:.1f} MB\n"
            f"Consumers        : {consumers_str}\n"
            f"Fetch Count      : {fetch_count} Today\n"
            f"Reuse Count      : {reuse_count}\n"
        )
        logger.info(log_str)
        
    def log_memory_snapshot(self, operation: str, memory_before_mb: float, memory_after_mb: float, dataset_size_mb: float):
        delta = memory_after_mb - memory_before_mb
        log_str = (
            f"\n[MEMORY SNAPSHOT] {operation}\n"
            f"Memory Before Fetch : {memory_before_mb:.1f} MB\n"
            f"Memory After Fetch  : {memory_after_mb:.1f} MB\n"
            f"Dataset Size        : {dataset_size_mb:.1f} MB\n"
            f"RSS Delta           : {delta:.1f} MB\n"
        )
        logger.info(log_str)
        self._record_rss(memory_after_mb)
        
        # Track temporary allocations or large data
        if dataset_size_mb > self.daily_metrics["largest_dataset"]["size_mb"]:
            self.daily_metrics["largest_dataset"] = {"name": operation, "size_mb": dataset_size_mb}
        if delta > self.daily_metrics["largest_temp_alloc"]["size_mb"]:
             self.daily_metrics["largest_temp_alloc"] = {"name": operation, "size_mb": delta}

    def log_cache_event(self, dataset_id: str, state: str, owner: str = None, consumers: list = None, size_mb: float = None, consumer: str = None, reason: str = None, recovered_mb: float = None):
        log_str = f"\n[CACHE]\nDataset ID:\n{dataset_id}\nState:\n{state}\n"
        if owner:
            log_str += f"Owner:\n{owner}\n"
        if consumers:
            log_str += f"Consumers:\n{chr(10).join(consumers)}\n"
        if consumer:
            log_str += f"Consumer:\n{consumer}\n"
            
        if state == "REUSED":
            curr_reuses = self.daily_metrics["dataset_reuse_counts"].get(dataset_id, 0) + 1
            self.daily_metrics["dataset_reuse_counts"][dataset_id] = curr_reuses
            if curr_reuses > self.daily_metrics["most_reused"]["count"]:
                 self.daily_metrics["most_reused"] = {"name": dataset_id, "count": curr_reuses}
                 
        if size_mb is not None:
            log_str += f"Size:\n{size_mb:.1f} MB\n"
            if size_mb > self.daily_metrics["largest_dataset"]["size_mb"]:
                self.daily_metrics["largest_dataset"] = {"name": dataset_id, "size_mb": size_mb}
        if reason:
            log_str += f"Reason:\n{reason}\n"
        if recovered_mb is not None:
            log_str += f"Recovered:\n{recovered_mb:.1f} MB\n"
            self.daily_metrics["memory_recovered_mb"] += recovered_mb
            
        logger.info(log_str)
        
    def log_dataset_size(self, dataset: str, rows: int, columns: int, estimated_mb: float):
        log_str = (
            f"\n[DATASET SIZE]\n"
            f"Dataset          : {dataset}\n"
            f"Rows             : {rows}\n"
            f"Columns          : {columns}\n"
            f"Estimated Size   : {estimated_mb:.1f} MB\n"
        )
        logger.info(log_str)
        
    def log_scanner_dependency(self, scanner: str, consumes: list, produces: list):
        log_str = (
            f"\n[SCANNER DEPENDENCY]\n"
            f"Scanner\n{scanner}\n"
            f"Consumes\n{chr(10).join(consumes) if consumes else 'None'}\n"
            f"Produces\n{chr(10).join(produces) if produces else 'None'}\n"
        )
        logger.info(log_str)
        

    def log_scheduler_event(self, name: str, event_type: str, error: str = None):
        msg = f"[{event_type}] Scheduler: {name}"
        if error:
            msg += f" (Error: {error})"
        logger.debug(msg)

    def log_scheduler(self, name: str, expected_time: str, started_time: str, delay_sec: float, runtime_sec: float, next_scheduled: str):
        log_str = (
            f"\n[SCHEDULER] {name}\n"
            f"Expected       : {expected_time}\n"
            f"Started        : {started_time}\n"
            f"Delay          : {delay_sec:.1f} sec\n"
            f"Runtime        : {runtime_sec:.1f} sec\n"
            f"Next Scheduled : {next_scheduled}\n"
        )
        logger.info(log_str)
        if delay_sec > self.daily_metrics["max_scheduler_drift"]:
            self.daily_metrics["max_scheduler_drift"] = delay_sec
            
        if runtime_sec > 0:
            if runtime_sec < self.daily_metrics["fastest_scanner"]["time"]:
                 self.daily_metrics["fastest_scanner"] = {"name": name, "time": runtime_sec}
            if runtime_sec > self.daily_metrics["slowest_scanner"]["time"]:
                 self.daily_metrics["slowest_scanner"] = {"name": name, "time": runtime_sec}
                 
    def log_session_timeline(self, time_str: str, event_desc: str):
        logger.info(f"\n[SESSION TIMELINE]\n{time_str}\n{event_desc}\n")
        
    def generate_daily_summary(self):
        avg_rss = self.daily_metrics["avg_rss_sum"] / self.daily_metrics["rss_samples"] if self.daily_metrics["rss_samples"] > 0 else 0
        min_rss_disp = self.daily_metrics["min_rss"] if self.daily_metrics["min_rss"] != float('inf') else 0
        
        fastest = self.daily_metrics["fastest_scanner"]
        slowest = self.daily_metrics["slowest_scanner"]
        fastest_disp = f"{fastest['name']} ({fastest['time']:.1f}s)" if fastest['name'] else "N/A"
        slowest_disp = f"{slowest['name']} ({slowest['time']:.1f}s)" if slowest['name'] else "N/A"
        largest_ds = f"{self.daily_metrics['largest_dataset']['name']} ({self.daily_metrics['largest_dataset']['size_mb']:.1f} MB)" if self.daily_metrics['largest_dataset']['name'] else "N/A"
        most_reused = f"{self.daily_metrics['most_reused']['name']} ({self.daily_metrics['most_reused']['count']} Uses)" if self.daily_metrics['most_reused']['name'] else "N/A"
        longest_fetch = f"{self.daily_metrics['longest_fetch']['name']} ({self.daily_metrics['longest_fetch']['time']:.1f}s)" if self.daily_metrics['longest_fetch']['name'] else "N/A"
        largest_temp = f"{self.daily_metrics['largest_temp_alloc']['name']} ({self.daily_metrics['largest_temp_alloc']['size_mb']:.1f} MB)" if self.daily_metrics['largest_temp_alloc']['name'] else "N/A"
        
        leak_cands = ",".join(self.daily_metrics['leak_candidates']) if self.daily_metrics['leak_candidates'] else "None"
        
        summary = (
            f"\n======================\n"
            f"Daily Summary\n"
            f"======================\n"
            f"Peak RSS             : {self.daily_metrics['peak_rss']:.1f} MB\n"
            f"Average RSS          : {avg_rss:.1f} MB\n"
            f"Minimum RSS          : {min_rss_disp:.1f} MB\n"
            f"Largest Dataset      : {largest_ds}\n"
            f"Largest Temp Alloc   : {largest_temp}\n"
            f"Most Reused Dataset  : {most_reused}\n"
            f"Longest Fetch        : {longest_fetch}\n"
            f"Slowest Scanner      : {slowest_disp}\n"
            f"Fastest Scanner      : {fastest_disp}\n"
            f"Cache Hits           : {self.daily_metrics['cache_hits']}\n"
            f"Cache Misses         : {self.daily_metrics['cache_misses']}\n"
            f"Duplicate Fetches    : {self.daily_metrics['duplicate_fetches']}\n"
            f"Scheduler Drift      : Max {self.daily_metrics['max_scheduler_drift']:.1f} sec\n"
            f"Memory Recovered     : {self.daily_metrics['memory_recovered_mb']:.1f} MB\n"
            f"Memory Not Recovered : {self.daily_metrics['memory_not_recovered_mb']:.1f} MB\n"
            f"Leak Candidates      : {leak_cands}\n"
            f"======================\n"
        )
        logger.info(summary)

# Singleton Instance
telemetry = TelemetryManager()
