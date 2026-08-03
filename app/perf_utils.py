"""
[VERSION: PERF_PROFILER_v2.0]
Stage 2 Audit — Performance & Observability Utilities

Provides:
  @profile_timing(name)           — decorator that logs stage timing + memory (v1.0)
  @stage_timer(label)             — fine-grained per-stage timer with ring-buffer telemetry (v2.0)
  flush_timing_report(path, ...)  — writes schema_version:1 JSON report from ring buffer (v2.0)
  FilterStats                     — per-filter rejection counter singleton
  log_api_cost(provider, hit_or_miss) — API cost tracker

Usage in scanners:
    from perf_utils import profile_timing, FilterStats, stage_timer, flush_timing_report

    @profile_timing("eod_scanner.run")
    def run_eod_scanner(...):
        stats = FilterStats("eod_scanner")
        for sym in universe:
            if not volume_ok(sym):
                stats.reject(sym, "volume")
                continue
            if not trend_ok(sym):
                stats.reject(sym, "trend")
                continue
            ...
        stats.log_summary()

Usage in fetchers:
    from perf_utils import log_api_cost
    log_api_cost("Upstox", cache_hit=False)
"""

import time
import threading
import functools
import logging
import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import defaultdict
from typing import Dict

try:
    import psutil
    _psutil_available = True
except ImportError:
    _psutil_available = False

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# ─── Profiling artifacts directory ────────────────────────────────────────────
_ARTIFACT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "artifacts", "profiling"
)
os.makedirs(_ARTIFACT_DIR, exist_ok=True)


# ─── Memory helper ────────────────────────────────────────────────────────────
def _rss_mb() -> float:
    """Current process RSS in MB. Returns 0.0 if psutil not available."""
    if not _psutil_available:
        return 0.0
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    except Exception:
        return 0.0


# ─── @profile_timing decorator ────────────────────────────────────────────────
def profile_timing(stage_name: str, log_to_file: bool = False):
    """
    [VERSION: PERF_PROFILER_v1.0]
    Decorator that wraps a function and logs:
      - Wall-clock duration (seconds)
      - Memory delta before/after (MB RSS)
      - Timestamp (IST)

    Args:
        stage_name: Human-readable stage label (e.g. 'eod_scanner.run')
        log_to_file: If True, also write JSON record to artifacts/profiling/
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            rss_before = _rss_mb()
            t0 = time.perf_counter()
            ts = datetime.now(IST).isoformat()
            result = None
            error = None
            try:
                result = fn(*args, **kwargs)
                return result
            except Exception as exc:
                error = str(exc)
                raise
            finally:
                elapsed = time.perf_counter() - t0
                rss_after = _rss_mb()
                rss_delta = rss_after - rss_before
                level = logging.WARNING if elapsed > 120 else logging.INFO
                logger.log(
                    level,
                    f"⏱  [{stage_name}] took {elapsed:.2f}s | "
                    f"RSS {rss_before:.0f}→{rss_after:.0f} MB (Δ{rss_delta:+.1f} MB)"
                    + (f" | ERROR: {error}" if error else "")
                )
                if log_to_file:
                    record = {
                        "stage": stage_name,
                        "timestamp": ts,
                        "duration_s": round(elapsed, 3),
                        "rss_before_mb": round(rss_before, 1),
                        "rss_after_mb": round(rss_after, 1),
                        "rss_delta_mb": round(rss_delta, 1),
                        "error": error,
                    }
                    fname = f"{stage_name.replace('.', '_')}_{datetime.now(IST).strftime('%Y%m%d')}.jsonl"
                    fpath = os.path.join(_ARTIFACT_DIR, fname)
                    try:
                        with open(fpath, "a") as f:
                            f.write(json.dumps(record) + "\n")
                    except Exception:
                        pass
        return wrapper
    return decorator


# ─── FilterStats ──────────────────────────────────────────────────────────────
class FilterStats:
    """
    [VERSION: PERF_PROFILER_v1.0]
    Thread-safe per-filter rejection counter for scanners.

    Usage:
        stats = FilterStats("eod_scanner")
        stats.reject(symbol, "volume")
        stats.reject(symbol, "trend")
        stats.pass_all(symbol)
        stats.log_summary()   # logs table + optionally writes CSV

    The log_summary() output looks like:
        Filter        Rejected    % of Universe
        volume           340          34.0%
        trend            210          21.0%
        rr_ratio          46           4.6%
        PASSED             4           0.4%
    """

    _lock = threading.Lock()

    def __init__(self, scanner_name: str):
        self.scanner_name = scanner_name
        self._rejections: Dict[str, int] = defaultdict(int)
        self._passed = 0
        self._total = 0

    def reject(self, symbol: str, filter_name: str):
        """Record that symbol was rejected by filter_name."""
        with self._lock:
            self._rejections[filter_name] += 1
            self._total += 1

    def pass_all(self, symbol: str):
        """Record that symbol passed all filters (generated alert candidate)."""
        with self._lock:
            self._passed += 1
            self._total += 1

    def log_summary(self, write_csv: bool = True):
        """Log rejection table and optionally write to artifacts/."""
        with self._lock:
            total = self._total or 1
            lines = [
                f"\n📊 Filter Stats — {self.scanner_name} "
                f"(universe={self._total}, passed={self._passed})",
                f"  {'Filter':<20} {'Rejected':>10}  {'% of Universe':>14}",
                f"  {'-'*46}",
            ]
            for fname, count in sorted(
                self._rejections.items(), key=lambda x: -x[1]
            ):
                pct = count / total * 100
                lines.append(f"  {fname:<20} {count:>10}  {pct:>13.1f}%")
            passed_pct = self._passed / total * 100
            lines.append(f"  {'PASSED':<20} {self._passed:>10}  {passed_pct:>13.1f}%")
            logger.info("\n".join(lines))

            if write_csv:
                self._write_csv(total)

    def _write_csv(self, total: int):
        """Write filter stats to artifacts/ CSV."""
        try:
            date_str = datetime.now(IST).strftime("%Y%m%d")
            fname = f"{self.scanner_name}_filter_stats_{date_str}.csv"
            fpath = os.path.join(_ARTIFACT_DIR, fname)
            with open(fpath, "w") as f:
                f.write("filter,rejected,pct_of_universe\n")
                for fname_k, count in sorted(
                    self._rejections.items(), key=lambda x: -x[1]
                ):
                    f.write(f"{fname_k},{count},{count/total*100:.1f}\n")
                f.write(f"PASSED,{self._passed},{self._passed/total*100:.1f}\n")
        except Exception:
            pass

    def reset(self):
        """Reset counters (call before each scanner run)."""
        with self._lock:
            self._rejections.clear()
            self._passed = 0
            self._total = 0


# ─── API Cost Tracker ─────────────────────────────────────────────────────────
_api_cost_lock = threading.Lock()
_api_cost: Dict[str, Dict[str, int]] = defaultdict(lambda: {"calls": 0, "cache_hits": 0})


def log_api_cost(provider: str, cache_hit: bool = False):
    """
    [VERSION: PERF_PROFILER_v1.0]
    Track API call counts and cache-hit ratio per provider.
    Call this inside every fetch function.

    Args:
        provider: 'Upstox', 'Fyers', 'Yahoo', 'BSE', etc.
        cache_hit: True if data was served from cache (no network call).
    """
    with _api_cost_lock:
        _api_cost[provider]["calls"] += 1
        if cache_hit:
            _api_cost[provider]["cache_hits"] += 1


def get_api_cost_report() -> dict:
    """Return API cost summary dict with hit ratios."""
    with _api_cost_lock:
        report = {}
        for provider, counts in _api_cost.items():
            calls = counts["calls"] or 1
            hits = counts["cache_hits"]
            report[provider] = {
                "calls": calls,
                "cache_hits": hits,
                "cache_miss": calls - hits,
                "hit_ratio_pct": round(hits / calls * 100, 1),
            }
        return report


def log_api_cost_summary():
    """Log the current API cost report to logger."""
    report = get_api_cost_report()
    if not report:
        return
    lines = ["\n📡 API Cost Report:"]
    lines.append(f"  {'Provider':<12} {'Calls':>8} {'Hits':>8} {'Miss':>8} {'HitRate':>10}")
    lines.append(f"  {'-'*50}")
    for prov, d in report.items():
        lines.append(
            f"  {prov:<12} {d['calls']:>8} {d['cache_hits']:>8} "
            f"{d['cache_miss']:>8} {d['hit_ratio_pct']:>9.1f}%"
        )
    logger.info("\n".join(lines))


def reset_api_cost():
    """Reset counters (call at start of each market cycle)."""
    with _api_cost_lock:
        _api_cost.clear()


# ─── Stage Timer Ring Buffer (v2.0) ───────────────────────────────────────────
# Thread-safe ring buffer accumulating per-stage call telemetry within a scan run.
# Flushed to disk via flush_timing_report() at end of each run.

_RING_BUFFER_MAX = 10_000
_stage_buffer: list = []
_stage_buffer_lock = threading.Lock()


def _get_sys_metrics() -> dict:
    """Capture lightweight system metrics. Returns empty dict if psutil unavailable."""
    if not _psutil_available:
        return {}
    try:
        import psutil, gc
        proc = psutil.Process(os.getpid())
        mem = proc.memory_info()
        gc_stats = gc.get_count()
        return {
            "rss_mb": round(mem.rss / 1024 / 1024, 1),
            "open_fds": proc.num_fds() if hasattr(proc, "num_fds") else None,
            "thread_count": proc.num_threads(),
            "gc_gen0": gc_stats[0],
            "gc_gen1": gc_stats[1],
            "gc_gen2": gc_stats[2],
        }
    except Exception:
        return {}


def stage_timer(label: str):
    """
    [VERSION: PERF_PROFILER_v2.0]
    Fine-grained per-stage decorator. Records each call's:
      - wall-clock duration (ms)
      - RSS start/end
      - timestamp (IST)

    All entries accumulate in a thread-safe ring buffer.
    Call flush_timing_report() at end of scan to emit the schema_version:1 report.

    Usage:
        @stage_timer("wealth_engine.indicator_calc")
        def calculate_wealth_technicals(sym, ...):
            ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            rss_before = _rss_mb()
            t0 = time.perf_counter()
            error = None
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                error = str(exc)
                raise
            finally:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                rss_after = _rss_mb()
                entry = {
                    "label": label,
                    "ts": datetime.now(IST).isoformat(),
                    "duration_ms": round(elapsed_ms, 3),
                    "rss_before_mb": round(rss_before, 1),
                    "rss_after_mb": round(rss_after, 1),
                    "error": error,
                }
                with _stage_buffer_lock:
                    _stage_buffer.append(entry)
                    if len(_stage_buffer) > _RING_BUFFER_MAX:
                        _stage_buffer.pop(0)
        return wrapper
    return decorator


def reset_stage_timers():
    """Clear ring buffer — call at the start of each scan run."""
    with _stage_buffer_lock:
        _stage_buffer.clear()


def flush_timing_report(
    phase: str,
    run_type: str = "cold_start",
    feature_flags: list = None,
    extra: dict = None,
) -> str:
    """
    [VERSION: PERF_PROFILER_v2.0]
    Aggregates ring buffer entries into a schema_version:1 timing report and
    writes it to artifacts/profiling/perf_<phase>_<date>.json.

    Returns the path of the written report file.

    Args:
        phase: Phase label e.g. "Phase0_Baseline", "Phase1_O1Lookup"
        run_type: "cold_start" or "warm_cache"
        feature_flags: list of active FEATURE_* flag names
        extra: additional fields to merge into report root
    """
    import gc

    with _stage_buffer_lock:
        entries = list(_stage_buffer)

    # Aggregate per-label stats
    from collections import defaultdict
    label_groups: dict = defaultdict(list)
    for e in entries:
        label_groups[e["label"]].append(e["duration_ms"])

    stages = {}
    for lbl, durations in label_groups.items():
        durations_sorted = sorted(durations)
        n = len(durations_sorted)
        stages[lbl] = {
            "calls": n,
            "total_ms": round(sum(durations_sorted), 2),
            "min_ms": round(durations_sorted[0], 3) if n else None,
            "p50_ms": round(durations_sorted[int(n * 0.50)], 3) if n else None,
            "p95_ms": round(durations_sorted[int(n * 0.95)], 3) if n else None,
            "p99_ms": round(durations_sorted[min(int(n * 0.99), n - 1)], 3) if n else None,
        }

    total_ms = sum(s["total_ms"] for s in stages.values())

    # System metrics snapshot
    sys_metrics: dict = {}
    if _psutil_available:
        try:
            import psutil
            proc = psutil.Process(os.getpid())
            mem = proc.memory_info()
            gc_counts = gc.get_count()
            sys_metrics = {
                "rss_mb": round(mem.rss / 1024 / 1024, 1),
                "thread_count": proc.num_threads(),
                "open_fds": proc.num_fds() if hasattr(proc, "num_fds") else None,
                "gc_gen0_count": gc_counts[0],
                "gc_gen1_count": gc_counts[1],
                "gc_gen2_count": gc_counts[2],
            }
            try:
                conns = proc.connections(kind="inet")
                sys_metrics["socket_count"] = len(conns)
            except Exception:
                pass
        except Exception:
            pass

    api_report = get_api_cost_report()

    report = {
        "schema_version": 1,
        "phase": phase,
        "run_timestamp": datetime.now(IST).isoformat(),
        "run_type": run_type,
        "feature_flags_active": feature_flags or [],
        "environment": sys_metrics,
        "stages": stages,
        "http": {
            prov: {"calls": d["calls"], "cache_hits": d["cache_hits"], "hit_ratio_pct": d["hit_ratio_pct"]}
            for prov, d in api_report.items()
        },
        "total_scan_wall_clock_ms": round(total_ms, 2),
    }
    if extra:
        report.update(extra)

    date_str = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    fname = f"perf_{phase}_{date_str}.json"
    fpath = os.path.join(_ARTIFACT_DIR, fname)
    try:
        with open(fpath, "w") as f:
            json.dump(report, f, indent=2)
        logger.info(f"📊 [PERF REPORT] Phase={phase} run_type={run_type} → {fpath} | Total={total_ms:.0f}ms")
    except Exception as e:
        logger.warning(f"⚠️ Failed to write timing report: {e}")

    return fpath
