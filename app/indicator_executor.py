# =====================================================================================
# app/indicator_executor.py
# [VERSION: V5_ACQUISITION_ROUTING_V1.0] Encapsulated Job-based IndicatorExecutor
# =====================================================================================

import os
import logging
import pandas as pd
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from technical_indicators import apply_indicators

logger = logging.getLogger(__name__)

def _process_single_indicator_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """Helper function to execute apply_indicators for a single job dictionary."""
    symbol = job.get("symbol", "")
    timeframe = job.get("timeframe", "1d")
    df = job.get("dataframe")
    daily_ohlc = job.get("daily_ohlc")

    if df is None or df.empty:
        return {"symbol": symbol, "dataframe": df, "error": "EMPTY_DF"}

    try:
        enriched_df = apply_indicators(df, timeframe=timeframe, daily_ohlc=daily_ohlc)
        return {"symbol": symbol, "dataframe": enriched_df, "error": None}
    except Exception as e:
        logger.warning(f"Error processing indicators for {symbol}: {e}")
        return {"symbol": symbol, "dataframe": df, "error": str(e)}


class IndicatorExecutor:
    """
    Encapsulates indicator computation strategy (Sequential vs ThreadPool vs ProcessPool).
    Clean caller interface: IndicatorExecutor.execute(jobs)
    Each job: {"symbol": str, "timeframe": str, "dataframe": DataFrame, "metadata": dict}
    """

    def __init__(self, mode: Optional[str] = None, max_workers: Optional[int] = None):
        # [VERSION: INDICATOR_THREAD_FIX_v1.0] Switch default from 'sequential' to 'thread' on Linux.
        # - 'process' mode was rightly avoided: macOS/spawn re-imports main.py in child processes,
        #   causing concurrent DB init and psycopg2 crashes. Linux fork() is safer but still risky.
        # - 'sequential' was chosen as safe fallback, BUT with 28 symbols × 3 concurrent TF threads
        #   = 84 sequential apply_indicators() calls, each ~4s → 236s total wall-clock delay.
        # - 'thread' mode is safe: ThreadPoolExecutor shares process memory (no re-import of main.py),
        #   and NumPy/pandas release the GIL during C-level calculations enabling genuine parallelism.
        # - Expected improvement: 28 symbols / 8 workers × ~4s = ~14s per TF thread (vs 112s sequential).
        # - Set INDICATOR_EXECUTION_MODE=sequential in .env to revert if instability is observed.
        import platform
        _default_mode = "thread" if platform.system() == "Linux" else "sequential"
        self.mode = mode or os.getenv("INDICATOR_EXECUTION_MODE", _default_mode)
        from config import SCAN_WORKER_THREADS
        self.max_workers = max_workers or int(os.getenv("INDICATOR_MAX_WORKERS", str(SCAN_WORKER_THREADS)))

    def execute(self, jobs: List[Dict[str, Any]]) -> Dict[str, pd.DataFrame]:
        """
        Executes indicator calculation over a list of job dicts.
        Returns a dict of symbol -> enriched DataFrame.
        """
        if not jobs:
            return {}

        results: Dict[str, pd.DataFrame] = {}

        if self.mode == "sequential" or len(jobs) <= 2:
            for job in jobs:
                res = _process_single_indicator_job(job)
                results[res["symbol"]] = res["dataframe"]
            return results

        if self.mode == "process":
            try:
                with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = [executor.submit(_process_single_indicator_job, job) for job in jobs]
                    for future in as_completed(futures):
                        res = future.result()
                        results[res["symbol"]] = res["dataframe"]
                return results
            except Exception as e:
                logger.warning(f"ProcessPoolExecutor failed ({e}); falling back to ThreadPoolExecutor.")
                self.mode = "thread"

        # Default: ThreadPoolExecutor (NumPy releases GIL during C calculations)
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(_process_single_indicator_job, job) for job in jobs]
            for future in as_completed(futures):
                res = future.result()
                results[res["symbol"]] = res["dataframe"]

        return results

# Default instance
indicator_executor = IndicatorExecutor()
