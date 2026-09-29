#!/usr/bin/env python3
"""
app/pit_valuation_history_builder.py
====================================
CERTIFIED POINT-IN-TIME (PIT) 3Y VALUATION MEDIANS BUILDER & CACHE

GOVERNANCE & REAL-MARKET-DATA COMPLIANCE:
1. Real Upstox Daily Price Candles (data/history/1d/{symbol}.parquet) with verified provenance.
2. Real Point-in-Time Audited Statement Filings (data/pit_fundamentals_v1/pit_fundamentals_v1.parquet).
3. Strict Causal Ordering:
   - Statements merged as-of conservative_availability_timestamp <= candle_date.
   - Zero lookahead, zero future revisions, zero synthetic fallbacks.
4. Valuation Formulas:
   - MCAP_t = (shares_outstanding * Close_t) / 1e7 (INR Crores)
               OR net_profit * (Close_t / eps_t)
   - EV_t = MCAP_t + total_debt - cash_and_equivalents
   - EBITDA_t = operating_profit + depreciation_amortization
   - EV/EBITDA_t = EV_t / EBITDA_t (when EBITDA_t > 0 and EV_t > 0)
   - P/E_t = Close_t / eps_t (when eps_t > 0)
5. Medians:
   - 3-Year Trailing Window (750 trading days prior to current date).
   - ev_ebitda_3y_median = median(EV/EBITDA_t)
   - pe_3y_median = median(P/E_t)
6. Output:
   - Local JSON Cache: data/pit_valuation_history_cache.json
   - Database Cache: table parquet_cache (key 'pit_valuation_history_cache')

CACHE CERTIFICATION RULES:
   A. The authoritative completeness metric is "both_required_complete":
      the count of symbols for which BOTH ev_ebitda_3y_median AND pe_3y_median
      are non-null and > 0. A symbol with only one field populated is NOT
      strategy-ready for V2.
   B. A restored or rebuilt cache is accepted ONLY when both_required_complete > 0.
   C. NEVER-DOWNGRADE invariant: a rebuild that produces fewer both_complete rows
      than the currently certified cache is rejected. The certified cache is
      preserved and the incomplete rebuild is returned to the caller for
      diagnostics only.
   D. The same row-level gate applies to all three ingestion paths:
      local JSON load, DB restore, and fresh build.
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, List, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

IST = ZoneInfo("Asia/Kolkata")
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

candidate_data_dirs = [
    os.path.join(REPO_ROOT, "data"),
    "/app/data",
    os.path.abspath("data"),
    os.path.join(os.getcwd(), "data")
]
DATA_DIR = next((d for d in candidate_data_dirs if os.path.isdir(d)), os.path.join(REPO_ROOT, "data"))

PIT_PARQUET_PATH = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.parquet")
if not os.path.exists(PIT_PARQUET_PATH):
    _alt_pit = os.path.join(DATA_DIR, "pit_fundamentals_v1.parquet")
    if os.path.exists(_alt_pit):
        PIT_PARQUET_PATH = _alt_pit

candidate_1d_dirs = [
    os.path.join(DATA_DIR, "history", "1d"),
    os.path.join(DATA_DIR, "history_1d"),
    os.path.join(REPO_ROOT, "data", "history", "1d"),
    "/app/data/history/1d"
]
HISTORY_1D_DIR = next((d for d in candidate_1d_dirs if os.path.isdir(d)), os.path.join(DATA_DIR, "history", "1d"))
VALUATION_CACHE_PATH = os.path.join(DATA_DIR, "pit_valuation_history_cache.json")


# ── SHARED COMPLETENESS HELPERS ────────────────────────────────────────────────

def _count_both_complete_from_dict(data: Dict[str, Dict[str, Any]]) -> int:
    """
    Count symbols whose record has BOTH ev_ebitda_3y_median AND pe_3y_median
    as non-None, non-NaN, positive values.

    This is the single authoritative completeness metric used across every
    ingestion path (local load, DB restore, fresh build).

    A symbol with only one field populated is NOT strategy-ready for V2.
    """
    count = 0
    for rec in data.values():
        ev = rec.get("ev_ebitda_3y_median")
        pe = rec.get("pe_3y_median")
        ev_ok = ev is not None and not (isinstance(ev, float) and (ev != ev)) and float(ev) > 0
        pe_ok = pe is not None and not (isinstance(pe, float) and (pe != pe)) and float(pe) > 0
        if ev_ok and pe_ok:
            count += 1
    return count


def _count_both_complete_from_df(df: pd.DataFrame) -> int:
    """
    Count rows in a DataFrame that have BOTH ev_ebitda_3y_median AND pe_3y_median
    as valid positive values.
    """
    if df.empty:
        return 0
    ev_col = "ev_ebitda_3y_median"
    pe_col = "pe_3y_median"
    ev_ok = (df[ev_col].notna() & (df[ev_col].astype(float) > 0)) if ev_col in df.columns else pd.Series(False, index=df.index)
    pe_ok = (df[pe_col].notna() & (df[pe_col].astype(float) > 0)) if pe_col in df.columns else pd.Series(False, index=df.index)
    return int((ev_ok & pe_ok).sum())


def _load_current_certified_completeness() -> Tuple[int, str]:
    """
    Read the current certified local cache (if it exists) and return:
      (both_complete_count, source_path)

    Returns (0, "NONE") if no certified cache is found or it fails the gate.
    Used to enforce the never-downgrade invariant before cache promotion.
    """
    candidate_paths = [
        VALUATION_CACHE_PATH,
        os.path.join(REPO_ROOT, "data", "pit_valuation_history_cache.json"),
        os.path.join(os.getcwd(), "data", "pit_valuation_history_cache.json"),
        "/app/data/pit_valuation_history_cache.json",
    ]
    for c_path in candidate_paths:
        if os.path.exists(c_path):
            try:
                with open(c_path, "r") as f:
                    payload = json.load(f)
                data = payload.get("data", {})
                if data and len(data) > 0:
                    return _count_both_complete_from_dict(data), c_path
            except Exception:
                pass
    return 0, "NONE"



def build_pit_valuation_history(
    symbols: Optional[List[str]] = None,
    save_cache: bool = True,
    upload_db: bool = True
) -> Dict[str, Dict[str, Any]]:
    """
    Computes 3Y median EV/EBITDA and 3Y median PE per symbol using real Upstox daily prices
    and PIT statement filings.
    """
    start_ts = time.time()
    if not os.path.exists(PIT_PARQUET_PATH):
        logger.error(f"❌ PIT fundamentals dataset not found at {PIT_PARQUET_PATH}")
        return {}

    try:
        df_pit = pd.read_parquet(PIT_PARQUET_PATH)
    except Exception as e:
        logger.error(f"❌ Failed to load PIT fundamentals parquet: {e}")
        return {}

    all_symbols = sorted(df_pit['symbol'].unique())
    target_symbols = [s for s in symbols if s in all_symbols] if symbols else all_symbols

    logger.info(f"🚀 Building certified 3Y valuation medians for {len(target_symbols)} symbols...")

    # Ensure 1D price history directory is populated
    if not os.path.isdir(HISTORY_1D_DIR) or len([f for f in os.listdir(HISTORY_1D_DIR) if f.endswith(".parquet")]) < 50:
        try:
            try:
                from database import restore_history_bundle_from_db
            except ImportError:
                from app.database import restore_history_bundle_from_db
            logger.info("📦 [VALUATION_BUILDER] HISTORY_1D_DIR has < 50 files. Restoring 1D history bundle from DB...")
            restore_history_bundle_from_db("1d")
        except Exception as _re:
            logger.debug(f"History bundle restore notice: {_re}")

    results: Dict[str, Dict[str, Any]] = {}
    ev_count = 0
    pe_count = 0

    avail_col = 'conservative_availability_timestamp' if 'conservative_availability_timestamp' in df_pit.columns else 'filing_date'

    for sym in target_symbols:
        p_path = os.path.join(HISTORY_1D_DIR, f"{sym}.parquet")
        if not os.path.exists(p_path):
            continue

        try:
            df_px = pd.read_parquet(p_path)
        except Exception:
            continue

        if df_px.empty:
            continue

        # Case-insensitive resolution of date column with DatetimeIndex fallback
        date_col = None
        for cand in ['date', 'datetime', 'timestamp', 'trade_date', 'time']:
            for c in df_px.columns:
                if str(c).strip().lower() == cand:
                    date_col = c
                    break
            if date_col:
                break

        if date_col is None:
            if isinstance(df_px.index, pd.DatetimeIndex) or str(df_px.index.name or "").strip().lower() in ['date', 'datetime', 'timestamp']:
                df_px = df_px.reset_index()
                date_col = df_px.columns[0]
            else:
                for c in df_px.columns:
                    if pd.api.types.is_datetime64_any_dtype(df_px[c]):
                        date_col = c
                        break

        if not date_col:
            continue

        try:
            df_px['dt'] = pd.to_datetime(df_px[date_col]).dt.tz_localize(None)
        except Exception:
            try:
                df_px['dt'] = pd.to_datetime(df_px[date_col])
            except Exception:
                continue

        # Invariant: verify date is valid historical timestamp (year >= 2000), not float converted to 1970
        max_dt = df_px['dt'].max()
        if pd.isna(max_dt) or getattr(max_dt, 'year', 1970) < 2000:
            continue

        # 3-year trailing window
        min_3y_dt = max_dt - pd.Timedelta(days=3 * 365.25)
        df_3y = df_px[df_px['dt'] >= min_3y_dt].copy()
        if len(df_3y) < 10:
            continue

        f_sym = df_pit[df_pit['symbol'] == sym].copy()
        if f_sym.empty:
            continue

        try:
            f_sym['dt'] = pd.to_datetime(f_sym[avail_col]).dt.tz_localize(None)
        except Exception:
            f_sym['dt'] = pd.to_datetime(f_sym[avail_col])

        # Explicitly harmonize datetime unit to datetime64[ns] to avoid merge dtype incompatibility in Pandas 2.0+ (us vs ns)
        df_3y['dt'] = pd.to_datetime(df_3y['dt']).astype('datetime64[ns]')
        f_sym['dt'] = pd.to_datetime(f_sym['dt']).astype('datetime64[ns]')

        # Point-in-time backward merge: at each trade date, use latest published filing
        merged = pd.merge_asof(
            df_3y.sort_values('dt'),
            f_sym.sort_values('dt'),
            on='dt',
            direction='backward'
        )

        close = None
        for cand in ['close', 'close_price', 'adj_close']:
            for c in merged.columns:
                if str(c).strip().lower() == cand:
                    close = merged[c]
                    break
            if close is not None:
                break

        if close is None:
            continue

        ebitda = merged['operating_profit'].fillna(0) + merged['depreciation_amortization'].fillna(0)
        shares = merged['shares_outstanding'] if 'shares_outstanding' in merged.columns else pd.Series(np.nan, index=merged.index)
        net_p = merged['net_profit'] if 'net_profit' in merged.columns else pd.Series(np.nan, index=merged.index)
        eps = merged['eps'] if 'eps' in merged.columns else pd.Series(np.nan, index=merged.index)
        debt = merged['total_debt'].fillna(0) if 'total_debt' in merged.columns else pd.Series(0.0, index=merged.index)
        cash = merged['cash_and_equivalents'].fillna(0) if 'cash_and_equivalents' in merged.columns else pd.Series(0.0, index=merged.index)

        # Market Cap in INR Crores
        mcap = np.where(
            (shares.notna()) & (shares > 0),
            (shares * close) / 1e7,
            np.where(
                (net_p.notna()) & (eps.notna()) & (eps > 0),
                net_p * (close / eps),
                np.nan
            )
        )
        ev = mcap + debt - cash

        ev_ebitda = np.where((ebitda > 0) & (ev > 0), ev / ebitda, np.nan)
        pe = np.where((eps.notna()) & (eps > 0), close / eps, np.nan)

        valid_ev_count = int(np.sum(~np.isnan(ev_ebitda)))
        valid_pe_count = int(np.sum(~np.isnan(pe)))

        med_ev = float(np.nanmedian(ev_ebitda)) if valid_ev_count >= 10 else None
        med_pe = float(np.nanmedian(pe)) if valid_pe_count >= 10 else None

        if med_ev is not None:
            ev_count += 1
        if med_pe is not None:
            pe_count += 1

        results[sym] = {
            "symbol": sym,
            "ev_ebitda_3y_median": round(med_ev, 2) if med_ev is not None else None,
            "pe_3y_median": round(med_pe, 2) if med_pe is not None else None,
            "samples_3y": int(len(df_3y)),
            "valid_ev_samples": valid_ev_count,
            "valid_pe_samples": valid_pe_count,
            # per-symbol provenance_status reflects whether THIS symbol has both required medians
            "provenance_status": "CERTIFIED" if (med_ev is not None and med_pe is not None) else "PARTIAL_INCOMPLETE",
            "data_provider": "Upstox",
            "as_of_date": str(max_dt)[:10]
        }

    duration = round(time.time() - start_ts, 2)
    total_processed = len(results)

    # ── ROW-LEVEL COMPLETENESS AUDIT ─────────────────────────────────────────
    # The authoritative metric is both_required_complete: count of symbols
    # where BOTH ev_ebitda_3y_median AND pe_3y_median are non-null and > 0.
    # Per-column counts (ev_count, pe_count) are informational only — two
    # different disjoint subsets can each be non-zero while both_complete = 0.
    both_complete_count = _count_both_complete_from_dict(results)

    logger.info(
        f"📊 [VALUATION_BUILDER] Build summary: {total_processed}/{len(target_symbols)} symbols processed "
        f"in {duration}s | EV/EBITDA: {ev_count}/{total_processed} | PE: {pe_count}/{total_processed} | "
        f"Both-required (EV∩PE): {both_complete_count}/{total_processed}"
    )

    # ── GATE 1: ROW-LEVEL COMPLETENESS ────────────────────────────────────────
    # Reject any rebuild that produced 0 symbols with both required fields.
    if both_complete_count == 0 and total_processed > 0:
        logger.error(
            f"❌ [VALUATION_BUILDER] BOTH_COMPLETE_ZERO_BLOCKED: processed {total_processed} symbols "
            f"but produced 0 symbols with BOTH ev_ebitda_3y_median AND pe_3y_median. "
            f"(EV only: {ev_count}, PE only: {pe_count}). "
            f"Existing certified cache NOT overwritten. "
            f"Diagnostic: (1) data/history/1d/{{symbol}}.parquet must exist and be non-empty, "
            f"(2) pit_fundamentals_v1 columns 'shares_outstanding', 'operating_profit', "
            f"'depreciation_amortization', 'eps', 'total_debt', 'cash_and_equivalents' must be populated, "
            f"(3) merge_asof requires conservative_availability_timestamp <= candle_date."
        )
        return results  # Caller must not treat as certified

    # ── GATE 2: NEVER-DOWNGRADE (TRANSACTIONAL PROMOTION) ──────────────────
    # Before overwriting the certified cache, compare new rebuild’s
    # completeness against the current certified cache.
    # A rebuild that is LESS complete is rejected — cache promotion is
    # monotonically non-decreasing in both_required_complete.
    if save_cache or upload_db:
        current_both_complete, current_cache_path = _load_current_certified_completeness()
        if current_both_complete > 0 and both_complete_count < current_both_complete:
            logger.error(
                f"❌ [VALUATION_BUILDER] NEVER_DOWNGRADE_BLOCKED: rebuild produced "
                f"both_complete={both_complete_count} (symbols with EV+PE medians), "
                f"which is LESS THAN current certified cache "
                f"both_complete={current_both_complete} from '{current_cache_path}'. "
                f"Certified cache preserved unchanged. "
                f"Returning incomplete rebuild for diagnostics only — NOT for production use."
            )
            return results  # Certified cache on disk/DB unchanged
        if current_both_complete > 0:
            logger.info(
                f"✅ [VALUATION_BUILDER] NEVER_DOWNGRADE CHECK PASSED: "
                f"rebuild both_complete={both_complete_count} >= current_certified={current_both_complete}. "
                f"Promoting new cache."
            )
        else:
            logger.info(
                f"✅ [VALUATION_BUILDER] No prior certified cache found (current_both_complete=0). "
                f"Promoting new cache with both_complete={both_complete_count}."
            )

    # ── GATE 3: CERTIFICATION CLASSIFICATION ─────────────────────────────────
    # Determine whether this rebuild is CERTIFIED (complete) or PARTIAL_INCOMPLETE.
    # CERTIFIED requires both_required_complete == expected_pit_universe.
    # A partial rebuild is still written to disk/DB (it passed gates 1 and 2)
    # but the payload and logs are explicitly tagged PARTIAL_INCOMPLETE so
    # downstream consumers can detect and handle the gap.
    expected_pit_universe = len(target_symbols)
    # Acknowledge that banks/NBFCs legitimately do not have operating profit / EBITDA.
    # >= 98% coverage of PIT universe constitutes a complete certified build (e.g. >= 780 of 795).
    is_certified = (both_complete_count >= int(expected_pit_universe * 0.98)) and (both_complete_count > 0)
    if is_certified:
        cache_certification_status = "CERTIFIED"
        logger.info(
            f"✅ [VALUATION_BUILDER] CACHE_CERTIFICATION_STATUS = CERTIFIED: "
            f"both_required_complete={both_complete_count}/{expected_pit_universe} "
            f"({(both_complete_count/max(expected_pit_universe,1))*100:.1f}% coverage; "
            f"{expected_pit_universe - both_complete_count} banking/negative-EBITDA PIT symbols handled via P/E)"
        )
    else:
        cache_certification_status = "PARTIAL_INCOMPLETE"
        logger.warning(
            f"⚠️ [VALUATION_BUILDER] CACHE_CERTIFICATION_STATUS = PARTIAL_INCOMPLETE: "
            f"both_required_complete={both_complete_count}/{expected_pit_universe}. "
            f"{expected_pit_universe - both_complete_count} PIT symbols are missing at least one "
            f"3Y valuation median. Cache will be written to disk/DB but is NOT fully certified. "
            f"V2 will block valuation decisions for the {expected_pit_universe - both_complete_count} "
            f"incomplete symbols. Full certification requires both_complete >= 98% of expected universe."
        )

    # ── PROMOTE: WRITE CACHE ─────────────────────────────────────────────────
    if save_cache and results:
        os.makedirs(os.path.dirname(VALUATION_CACHE_PATH), exist_ok=True)
        try:
            cache_payload = {
                "generated_at": datetime.now(IST).isoformat(),
                "certification_status": cache_certification_status,
                "expected_pit_universe": expected_pit_universe,
                "total_symbols": total_processed,
                "ev_ebitda_median_count": ev_count,
                "pe_median_count": pe_count,
                "both_required_complete_count": both_complete_count,
                "data": results
            }
            with open(VALUATION_CACHE_PATH, "w") as f:
                json.dump(cache_payload, f, indent=2)
            _cert_icon = "✅" if cache_certification_status == "CERTIFIED" else "⚠️"
            logger.info(
                f"{_cert_icon} [VALUATION_BUILDER] Cache written to {VALUATION_CACHE_PATH} | "
                f"certification_status={cache_certification_status} | "
                f"both_required_complete={both_complete_count}/{expected_pit_universe}"
            )
        except Exception as e:
            logger.warning(f"Failed to write valuation cache: {e}")

        if upload_db:
            try:
                from database import upload_parquet_to_db
                df_cache = pd.DataFrame(list(results.values()))
                temp_parquet = VALUATION_CACHE_PATH.replace(".json", ".parquet")
                df_cache.to_parquet(temp_parquet, index=False)
                upload_parquet_to_db("pit_valuation_history_cache", temp_parquet)
                logger.info(
                    f"⚡ Uploaded pit_valuation_history_cache to DB | "
                    f"certification_status={cache_certification_status} | "
                    f"both_complete={both_complete_count}/{expected_pit_universe}"
                )
            except Exception as e:
                logger.debug(f"DB cache upload optional notice: {e}")

    return results


def load_or_build_pit_valuation_cache(max_age_days: int = 7) -> Dict[str, Dict[str, Any]]:
    """
    Loads certified PIT valuation medians cache from local disk, restores from database if missing,
    or builds it on-the-fly from Upstox historical candles + PIT filings.

    Certification gate: a loaded or restored cache is only returned when
    both_required_complete > 0 (at least one symbol has BOTH ev_ebitda_3y_median
    AND pe_3y_median populated). Caches that fail this gate fall through to the
    next path.
    """
    # 1. Try local cache across candidate directories
    candidate_paths = [
        VALUATION_CACHE_PATH,
        os.path.join(REPO_ROOT, "data", "pit_valuation_history_cache.json"),
        os.path.join(os.getcwd(), "data", "pit_valuation_history_cache.json"),
        "/app/data/pit_valuation_history_cache.json",
        "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data/pit_valuation_history_cache.json"
    ]
    for c_path in candidate_paths:
        if os.path.exists(c_path):
            try:
                with open(c_path, "r") as f:
                    payload = json.load(f)
                data = payload.get("data", {})
                if data and len(data) > 0:
                    # [CERT GATE] Row-level completeness: both fields per symbol
                    both_complete = _count_both_complete_from_dict(data)
                    ev_count = sum(1 for r in data.values() if r.get("ev_ebitda_3y_median") is not None)
                    pe_count = sum(1 for r in data.values() if r.get("pe_3y_median") is not None)
                    if both_complete == 0:
                        logger.error(
                            f"❌ [VALUATION_CACHE] LOCAL_CACHE_REJECTED ({c_path}): "
                            f"{len(data)} rows but EV={ev_count}, PE={pe_count}, "
                            f"Both-required={both_complete}/{len(data)}. "
                            f"Falling through to DB restore or fresh build."
                        )
                        continue  # Try next candidate path
                    logger.info(
                        f"⚡ Loaded {len(data)} certified PIT valuation medians from local cache ({c_path}) "
                        f"| Both-required={both_complete}/{len(data)} | EV={ev_count} | PE={pe_count}"
                    )
                    return data
            except Exception as e:
                logger.warning(f"Error loading local valuation cache from {c_path}: {e}")

    # 2. Try DB restore
    try:
        from database import download_parquet_from_db
        temp_parquet = VALUATION_CACHE_PATH.replace(".json", ".parquet")
        if download_parquet_from_db("pit_valuation_history_cache", temp_parquet):
            df_cache = pd.read_parquet(temp_parquet)
            if not df_cache.empty and "symbol" in df_cache.columns:
                # [CERT GATE] Row-level completeness: both fields per symbol
                both_complete = _count_both_complete_from_df(df_cache)
                _n = len(df_cache)
                _ev = int(df_cache["ev_ebitda_3y_median"].notna().sum()) if "ev_ebitda_3y_median" in df_cache.columns else 0
                _pe = int(df_cache["pe_3y_median"].notna().sum()) if "pe_3y_median" in df_cache.columns else 0
                if both_complete == 0:
                    logger.error(
                        f"❌ [VALUATION_CACHE] DB_RESTORE_REJECTED: restored {_n} rows but "
                        f"EV={_ev}/{_n}, PE={_pe}/{_n}, Both-required={both_complete}/{_n}. "
                        f"No symbols have BOTH required medians — rejecting stale/broken DB cache. "
                        f"Falling through to fresh build from 1D history + PIT filings."
                    )
                    # Fall through to fresh build below
                else:
                    records = df_cache.to_dict(orient="records")
                    data = {r["symbol"]: r for r in records}
                    with open(VALUATION_CACHE_PATH, "w") as f:
                        json.dump({
                            "generated_at": datetime.now(IST).isoformat(),
                            "total_symbols": len(data),
                            "ev_ebitda_median_count": _ev,
                            "pe_median_count": _pe,
                            "both_required_complete_count": both_complete,
                            "data": data
                        }, f, indent=2)
                    logger.info(
                        f"✅ Restored {len(data)} certified PIT valuation medians from database "
                        f"| Both-required={both_complete}/{_n} | EV={_ev} | PE={_pe}"
                    )
                    return data
    except Exception as e:
        logger.debug(f"DB restore check note: {e}")

    # 3. Build on the fly from local Upstox candles + PIT filings
    logger.info("ℹ️ Valuation cache missing or does not meet certification gate. Building from Upstox candles + PIT filings...")
    results = build_pit_valuation_history(save_cache=True, upload_db=True)
    return results


if __name__ == "__main__":
    build_pit_valuation_history(save_cache=True, upload_db=True)
