import gc
import os
import sys
import time
import logging
import psutil
import tracemalloc
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

class MemoryProfiler:
    """
    A context manager to profile memory and execution time for a block of code.
    Logs Current RSS, Peak RSS, Elapsed Time, and optional GC statistics.
    """
    def __init__(self, stage_name: str, force_gc_cleanup: bool = False):
        self.stage_name = stage_name
        self.force_gc_cleanup = force_gc_cleanup
        self.process = psutil.Process(os.getpid())
        
        self.start_time = 0
        self.start_rss = 0
        self.start_peak = 0
        
    def __enter__(self):
        self.start_time = time.monotonic()
        mem = self.process.memory_info()
        self.start_rss = mem.rss
        
        # Cross-platform peak tracking
        if hasattr(mem, 'peak_wset'):  # Windows
            self.start_peak = mem.peak_wset
        else:
            self.start_peak = self.start_rss  # Will approximate for Linux/macOS
            
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.force_gc_cleanup:
            # Force GC to see if memory is actually retained or just pending collection
            collected = gc.collect()
            logger.info(f"[MEMORY GC] Stage: {self.stage_name} | Reclaimed: {collected} objects")

        mem = self.process.memory_info()
        end_rss = mem.rss
        
        if hasattr(mem, 'peak_wset'):
            peak_rss = mem.peak_wset
        else:
            # Approximation: If end > start_peak, end is new peak. Otherwise assume start_peak.
            peak_rss = max(self.start_peak, end_rss)
            
        elapsed = time.monotonic() - self.start_time
        delta_rss = end_rss - self.start_rss
        
        current_mb = end_rss / (1024 * 1024)
        peak_mb = peak_rss / (1024 * 1024)
        delta_mb = delta_rss / (1024 * 1024)
        
        delta_str = f"+{delta_mb:.1f}" if delta_mb >= 0 else f"{delta_mb:.1f}"
        
        logger.info(
            f"[MEMORY] Stage: {self.stage_name:<15} | "
            f"Time: {elapsed:>5.1f}s | "
            f"Current: {current_mb:>6.1f} MB | "
            f"Peak: {peak_mb:>6.1f} MB | "
            f"Delta: {delta_str:>6} MB"
        )
        
        # Budget Alerts
        current_gb = current_mb / 1024
        if current_gb >= 3.0:
            logger.error(f"🚨 CRITICAL MEMORY USAGE: {current_gb:.2f} GB! Threshold (3 GB) exceeded.")
        elif current_gb >= 2.0:
            logger.warning(f"⚠️ WARNING MEMORY USAGE: {current_gb:.2f} GB! Threshold (2 GB) exceeded.")


def start_tracemalloc():
    """Starts tracemalloc for object tracking."""
    if not tracemalloc.is_tracing():
        tracemalloc.start(10)

def log_object_inventory():
    """
    Logs top tracemalloc lines and specifically sizes all pandas/numpy objects in memory.
    """
    if not tracemalloc.is_tracing():
        logger.warning("[INVENTORY] tracemalloc is not running.")
        return
        
    snapshot = tracemalloc.take_snapshot()
    top_stats = snapshot.statistics('lineno')

    logger.info("=== [INVENTORY] Top 5 Python Allocations (tracemalloc) ===")
    for stat in top_stats[:5]:
        logger.info(f"  {stat}")

    logger.info("=== [INVENTORY] Environment & GC State ===")
    arena_max = os.environ.get('MALLOC_ARENA_MAX', 'Not Set')
    logger.info(f"  MALLOC_ARENA_MAX: {arena_max}")
    logger.info(f"  GC Thresholds: {gc.get_threshold()}")
    logger.info(f"  GC Object Counts: {gc.get_count()}")

    # Explicitly calculate Pandas / NumPy sizes which tracemalloc misses
    logger.info("=== [INVENTORY] Deep Native Sizing ===")
    
    total_pd_bytes = 0
    total_np_bytes = 0
    pd_count = 0
    np_count = 0
    
    # gc.get_objects() is extremely slow and pauses the world. Only run occasionally.
    objects = gc.get_objects()
    for obj in objects:
        if isinstance(obj, pd.DataFrame):
            try:
                # deep=True ensures we count strings/objects properly
                total_pd_bytes += obj.memory_usage(deep=True).sum()
                pd_count += 1
            except Exception:
                pass
        elif isinstance(obj, np.ndarray):
            try:
                total_np_bytes += obj.nbytes
                np_count += 1
            except Exception:
                pass
                
    pd_mb = total_pd_bytes / (1024 * 1024)
    np_mb = total_np_bytes / (1024 * 1024)
    
    logger.info(f"[INVENTORY] Pandas: {pd_mb:.1f} MB ({pd_count} objects)")
    logger.info(f"[INVENTORY] NumPy: {np_mb:.1f} MB ({np_count} objects)")
    logger.info("=======================================")

def _inventory_worker():
    while True:
        time.sleep(3600)  # Log every 1 hour
        try:
            log_object_inventory()
        except Exception as e:
            logger.warning(f"Failed to log object inventory: {e}")

# Start tracemalloc immediately on module load
start_tracemalloc()

import threading
threading.Thread(target=_inventory_worker, name="MemoryInventory", daemon=True).start()

def chunk_iterable(iterable, batch_size):
    """Safely chunks a list or pandas DataFrame."""
    total_len = len(iterable)
    for i in range(0, total_len, batch_size):
        if isinstance(iterable, pd.DataFrame):
            yield iterable.iloc[i:i + batch_size]
        else:
            yield iterable[i:i + batch_size]

class BatchMemoryTracker:
    """
    Context manager to track memory through a typical Fetch -> Process -> Cleanup batch lifecycle.
    """
    def __init__(self, stage_name: str, batch_num: int, total_batches: int, item_count: int, collect_gc: bool = False):
        self.stage_name = stage_name
        self.batch_num = batch_num
        self.total_batches = total_batches
        self.item_count = item_count
        self.collect_gc = collect_gc
        self.process = psutil.Process(os.getpid())
        
        self.start_time = 0
        self.rss_before = 0
        self.rss_after_fetch = 0
        self.row_count = 0

    def __enter__(self):
        self.start_time = time.monotonic()
        self.rss_before = self.process.memory_info().rss / (1024 * 1024)
        return self

    def mark_fetch_complete(self, row_count: int = 0):
        self.row_count = row_count
        self.rss_after_fetch = self.process.memory_info().rss / (1024 * 1024)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.collect_gc:
            gc.collect()
            
        rss_after_cleanup = self.process.memory_info().rss / (1024 * 1024)
        elapsed = time.monotonic() - self.start_time
        
        # If fetch was never explicitly marked, just use cleanup memory for both
        if self.rss_after_fetch == 0:
            self.rss_after_fetch = rss_after_cleanup
            
        logger.info(
            f"[{self.stage_name} Batch {self.batch_num}/{self.total_batches}] "
            f"Symbols: {self.item_count} | Rows: {self.row_count} | "
            f"RSS Before: {self.rss_before:.1f} MB | "
            f"RSS After Fetch: {self.rss_after_fetch:.1f} MB | "
            f"RSS After Cleanup: {rss_after_cleanup:.1f} MB | "
            f"Elapsed: {elapsed:.1f}s"
        )
