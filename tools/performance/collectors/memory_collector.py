import os
import psutil
import tracemalloc
import json

class MemoryCollector:
    """
    Collects peak RSS, current RSS, and tracemalloc peaks during performance test runs.
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MemoryCollector, cls).__new__(cls)
            cls._instance.process = psutil.Process(os.getpid())
            cls._instance.start_rss = 0
            cls._instance.peak_rss = 0
            cls._instance.metrics = {}
        return cls._instance

    @classmethod
    def start_run(cls):
        col = cls()
        col.start_rss = col.process.memory_info().rss
        col.peak_rss = col.start_rss
        if not tracemalloc.is_tracing():
            tracemalloc.start()
        tracemalloc.clear_traces()

    @classmethod
    def snapshot(cls, label: str):
        col = cls()
        current_rss = col.process.memory_info().rss
        if hasattr(col.process.memory_info(), 'peak_wset'):
            col.peak_rss = max(col.peak_rss, col.process.memory_info().peak_wset)
        else:
            col.peak_rss = max(col.peak_rss, current_rss)
            
        peak_alloc_bytes = tracemalloc.get_traced_memory()[1] if tracemalloc.is_tracing() else 0
        
        col.metrics[label] = {
            "rss_mb": current_rss / (1024 * 1024),
            "peak_rss_mb": col.peak_rss / (1024 * 1024),
            "tracemalloc_peak_mb": peak_alloc_bytes / (1024 * 1024)
        }

    @classmethod
    def get_metrics(cls) -> dict:
        return cls().metrics

    @classmethod
    def dump_metrics(cls, filepath: str):
        with open(filepath, 'w') as f:
            json.dump(cls.get_metrics(), f, indent=4)
