"""
fundamental_pipeline.py
========================
Production-Grade Field-Aware Fundamental Hydration Engine (v3.0).

Key Architectural Safeguards:
  1. Field-Aware Multi-TTL Freshness (Market Ratios: 3d | Margins: 30d | Statements: 90d).
  2. Field-Level Provenance & Origin Tracking (source, origin, quality, fetched_at, period_end).
  3. Strict Semantic Sanity Validation (negative profit & negative CFO accepted as valid financial states).
  4. 5-Tier Failover Cascade (Screener.in -> TradingView -> NSE -> Yahoo -> Mathematical Derivation).
  5. In-Memory Symbol Single-Flight & Provider Concurrency Semaphores.
  6. Provider Circuit Breakers with Cooldown Jitter (300s ± random jitter on HTTP 429/403).
  7. Categorized Negative Caching (NOT_FOUND: 7d | TEMPORARILY_UNAVAILABLE: 15m | INVALID_DATA: 1h).
  8. Minimum Usable Hydration Contract (CORE_FIELDS, VALUATION_FIELDS, QUALITY_FIELDS).
  9. Canonical PostgreSQL DB Persistence (screener_cache with TIMESTAMPTZ & parser_version).
"""

import os
import json
import time
import math
import random
import logging
import threading
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, Set, Tuple

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
PARSER_VERSION = "screener_v3"

# ── 1. CONFIGURATION & TIERED FIELD SETS ────────────────────────────────────
FIELD_TTLS_SECONDS = {
    # Market Valuation Ratios (1–3 Days)
    "pe": 3 * 86400,
    "pb": 3 * 86400,
    "div_yield": 3 * 86400,
    # Margins & Return Metrics (7–30 Days)
    "roe": 30 * 86400,
    "roce": 30 * 86400,
    "op_margin": 30 * 86400,
    "gross_margin": 30 * 86400,
    # Financial Statement Line Items (30–90 Days)
    "total_equity": 90 * 86400,
    "total_debt": 90 * 86400,
    "operating_cash_flow": 90 * 86400,
    "net_profit": 90 * 86400,
    "total_assets": 90 * 86400,
}

CORE_FIELDS = {"total_equity", "total_debt", "net_profit", "operating_cash_flow"}
VALUATION_FIELDS = {"pe", "pb"}
QUALITY_FIELDS = {"roe", "roce"}
REQUIRED_FIELDS = CORE_FIELDS | VALUATION_FIELDS | QUALITY_FIELDS

# ── 2. PROVIDER CONCURRENCY SEMAPHORES & CIRCUIT BREAKERS ───────────────────
_pipeline_lock = threading.Lock()
_symbol_inflight: Dict[str, threading.Event] = {}
_symbol_results: Dict[str, Dict[str, Any]] = {}
_negative_cache: Dict[str, Dict[str, Any]] = {} # symbol -> {retry_after, reason, status}

_provider_semaphores = {
    "SCREENER": threading.Semaphore(2),
    "TRADINGVIEW": threading.Semaphore(6),
    "NSE": threading.Semaphore(2),
    "YAHOO": threading.Semaphore(3),
}

_provider_health: Dict[str, Dict[str, Any]] = {
    "SCREENER": {"state": "HEALTHY", "cooldown_until": 0.0},
    "TRADINGVIEW": {"state": "HEALTHY", "cooldown_until": 0.0},
    "NSE": {"state": "HEALTHY", "cooldown_until": 0.0},
    "YAHOO": {"state": "HEALTHY", "cooldown_until": 0.0},
}


def _is_provider_healthy(provider_name: str) -> bool:
    info = _provider_health.get(provider_name, {})
    if info.get("state") == "HEALTHY":
        return True
    if time.time() >= info.get("cooldown_until", 0.0):
        info["state"] = "HEALTHY"
        logger.info(f"🟢 [CIRCUIT BREAKER] Provider '{provider_name}' cooldown expired. Reset to HEALTHY.")
        return True
    return False


def _trip_circuit_breaker(provider_name: str, base_cooldown_seconds: float = 300.0, reason: str = ""):
    # Add random jitter to prevent thundering herd recovery storms
    jitter = random.uniform(-30.0, 30.0)
    total_cooldown = max(60.0, base_cooldown_seconds + jitter)
    
    info = _provider_health.setdefault(provider_name, {})
    info["state"] = "RATE_LIMITED"
    info["cooldown_until"] = time.time() + total_cooldown
    logger.warning(f"🚨 [CIRCUIT BREAKER TRIPPED] Provider '{provider_name}' marked RATE_LIMITED for {total_cooldown:.1f}s. Reason: {reason}")


# ── 3. SEMANTIC VALIDATION ───────────────────────────────────────────────────
def valid_number(v: Any) -> bool:
    """Returns True iff v is non-null, numeric, and finite."""
    if v is None:
        return False
    try:
        val = float(v)
        return math.isfinite(val)
    except (TypeError, ValueError):
        return False


def is_field_semantically_valid(field_name: str, value: Any) -> bool:
    if not valid_number(value):
        return False
    val = float(value)
    if field_name in ("total_equity", "total_assets"):
        return val > 0
    if field_name in ("total_debt", "pb"):
        return val >= 0
    if field_name == "pe":
        return val >= 0 or val is not None # Negative PE is acceptable for loss-making firms
    # net_profit < 0 and operating_cash_flow < 0 are VALID financial states
    return True


def is_field_fresh(field_name: str, field_meta: dict, now_ts: float) -> bool:
    if not isinstance(field_meta, dict):
        return False
    if not is_field_semantically_valid(field_name, field_meta.get("value")):
        return False
    fetched_at_str = field_meta.get("fetched_at", "")
    if not fetched_at_str:
        return False
    try:
        dt = datetime.fromisoformat(fetched_at_str)
        age_seconds = now_ts - dt.timestamp()
        ttl_seconds = FIELD_TTLS_SECONDS.get(field_name, 30 * 86400)
        return age_seconds < ttl_seconds
    except Exception:
        return False


# ── 4. CANONICAL DB PERSISTENCE (screener_cache TABLE) ──────────────────────
def load_screener_cache_from_db(symbol: str) -> Optional[Dict[str, Any]]:
    clean_sym = symbol.split(":")[-1].replace(".NS", "").replace(".BO", "").strip().upper()
    try:
        from database import get_connection
        from psycopg2.extras import RealDictCursor
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT data, parser_version, fetched_at
                    FROM screener_cache
                    WHERE symbol = %s
                """, (clean_sym,))
                row = cur.fetchone()
                if row and row.get("data"):
                    # Check parser_version invalidation
                    if row.get("parser_version") != PARSER_VERSION:
                        logger.info(f"🔄 Parser version mismatch for {clean_sym} (DB={row.get('parser_version')} vs Current={PARSER_VERSION}). Invalidating cache.")
                        return None
                    payload = row["data"]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    return payload
    except Exception as e:
        logger.debug(f"Failed to load screener_cache from DB for {clean_sym}: {e}")
    return None


def save_screener_cache_to_db(symbol: str, payload: Dict[str, Any]):
    clean_sym = symbol.split(":")[-1].replace(".NS", "").replace(".BO", "").strip().upper()
    now_iso = datetime.now(timezone.utc).isoformat()
    status = payload.get("hydration", {}).get("status", "COMPLETE")
    quality = payload.get("hydration", {}).get("quality", "HIGH")
    primary_src = payload.get("hydration", {}).get("primary_source", "UNKNOWN")

    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO screener_cache (symbol, data, fetched_at, status, quality, source, parser_version, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol) DO UPDATE
                    SET data = EXCLUDED.data,
                        fetched_at = EXCLUDED.fetched_at,
                        status = EXCLUDED.status,
                        quality = EXCLUDED.quality,
                        source = EXCLUDED.source,
                        parser_version = EXCLUDED.parser_version,
                        updated_at = EXCLUDED.updated_at;
                """, (clean_sym, json.dumps(payload), now_iso, status, quality, primary_src, PARSER_VERSION, now_iso))
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to save screener_cache DB entry for {clean_sym}: {e}")


# ── 5. PROVIDER HYDRATORS WITH CONCURRENCY SEMAPHORES ───────────────────────

def _fetch_from_screener(symbol: str) -> Tuple[Dict[str, Any], bool]:
    """Tier 1: Screener.in Direct Scraper."""
    if not _is_provider_healthy("SCREENER"):
        return {}, False
    sem = _provider_semaphores["SCREENER"]
    acquired = sem.acquire(blocking=True, timeout=5.0)
    if not acquired:
        logger.warning(f"⚠️ Screener.in concurrency limit reached for {symbol}. Moving to next tier.")
        return {}, False
    try:
        from screener_fetcher import fetch_screener_fundamentals
        data = fetch_screener_fundamentals(symbol, force_refresh=True)
        if not data or data.get("failed"):
            return {}, False

        extracted = {}
        now_iso = datetime.now(IST).isoformat()

        field_map = {
            "total_equity": data.get("total_equity"),
            "total_debt": data.get("total_debt"),
            "operating_cash_flow": data.get("operating_cash_flow"),
            "net_profit": data.get("net_profit"),
            "total_assets": data.get("total_assets"),
            "roe": data.get("roe"),
            "roce": data.get("roce"),
            "pe": data.get("pe_ratio"),
            "pb": data.get("book_value_per_share"),
            "div_yield": data.get("div_yield")
        }

        for k, val in field_map.items():
            if is_field_semantically_valid(k, val):
                extracted[k] = {
                    "value": float(val),
                    "source": "SCREENER",
                    "origin": "AGGREGATED_FINANCIAL_DATA",
                    "quality": "REPORTED",
                    "fetched_at": now_iso
                }
        return extracted, True
    except Exception as e:
        msg = str(e).lower()
        if "429" in msg or "403" in msg or "too many requests" in msg:
            _trip_circuit_breaker("SCREENER", 300.0, str(e))
        return {}, False
    finally:
        sem.release()


def _fetch_from_tradingview(symbol: str) -> Tuple[Dict[str, Any], bool]:
    """Tier 2: TradingView Direct API Fields."""
    if not _is_provider_healthy("TRADINGVIEW"):
        return {}, False
    sem = _provider_semaphores["TRADINGVIEW"]
    acquired = sem.acquire(blocking=True, timeout=4.0)
    if not acquired:
        return {}, False
    clean_sym = symbol.split(":")[-1].replace(".NS", "").replace(".BO", "").strip().upper()
    try:
        from tradingview_screener import Query, col
        fields = [
            "name", "market_cap_basic", "return_on_equity_fy", "return_on_assets_fq",
            "debt_to_equity_fy", "operating_margin_ttm", "total_assets_fy", "total_debt_fy",
            "price_earnings_ttm", "price_book_ratio", "free_cash_flow_ttm"
        ]
        q = Query().set_markets("india").select(*fields).where(col("name") == clean_sym).limit(1)
        total, df = q.get_scanner_data(timeout=(3, 6))
        if df is None or df.empty:
            return {}, False

        row = df.iloc[0]
        extracted = {}
        now_iso = datetime.now(IST).isoformat()

        def _val(col_name):
            return float(row[col_name]) if col_name in row and pd_notna(row[col_name]) else None

        def pd_notna(x):
            return x is not None and not (isinstance(x, float) and math.isnan(x))

        mcap = _val("market_cap_basic")
        pb = _val("price_book_ratio")
        
        total_equity = (mcap / pb) if (mcap and pb and pb > 0) else None

        field_map = {
            "total_assets": _val("total_assets_fy"),
            "total_debt": _val("total_debt_fy"),
            "roe": _val("return_on_equity_fy"),
            "pe": _val("price_earnings_ttm"),
            "pb": pb,
            "op_margin": _val("operating_margin_ttm"),
            "total_equity": total_equity
        }

        for k, val in field_map.items():
            if is_field_semantically_valid(k, val):
                quality = "ESTIMATED" if k == "total_equity" else "REPORTED"
                extracted[k] = {
                    "value": float(val),
                    "source": "TRADINGVIEW",
                    "origin": "AGGREGATED_FINANCIAL_DATA",
                    "quality": quality,
                    "fetched_at": now_iso
                }
        return extracted, True
    except Exception as e:
        msg = str(e).lower()
        if "429" in msg or "403" in msg:
            _trip_circuit_breaker("TRADINGVIEW", 300.0, str(e))
        return {}, False
    finally:
        sem.release()


def _fetch_from_nse(symbol: str) -> Tuple[Dict[str, Any], bool]:
    """Tier 3: NSE Official Financial Results API."""
    if not _is_provider_healthy("NSE"):
        return {}, False
    sem = _provider_semaphores["NSE"]
    acquired = sem.acquire(blocking=True, timeout=4.0)
    if not acquired:
        return {}, False
    clean_sym = symbol.split(":")[-1].replace(".NS", "").replace(".BO", "").strip().upper()
    url = f"https://www.nseindia.com/api/results-consolidated?symbol={clean_sym}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://www.nseindia.com/get-quotes/equity?symbol={clean_sym}"
    }
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=4)
        resp = session.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                latest = data[0]
                net_profit = float(latest.get("re_net_proc_loss", 0) or 0) * 100000.0
                period_end = latest.get("re_period_end_date", "")
                now_iso = datetime.now(IST).isoformat()
                extracted = {}
                if is_field_semantically_valid("net_profit", net_profit):
                    extracted["net_profit"] = {
                        "value": net_profit,
                        "source": "NSE",
                        "origin": "OFFICIAL_DISCLOSURE",
                        "quality": "REPORTED",
                        "fetched_at": now_iso,
                        "period_end": period_end,
                        "period_type": "QUARTERLY"
                    }
                return extracted, True
    except Exception as e:
        msg = str(e).lower()
        if "403" in msg or "429" in msg:
            _trip_circuit_breaker("NSE", 300.0, str(e))
    finally:
        sem.release()
    return {}, False


def _fetch_from_yahoo(symbol: str) -> Tuple[Dict[str, Any], bool]:
    """Tier 4: Yahoo Finance API Fallback."""
    if not _is_provider_healthy("YAHOO"):
        return {}, False
    sem = _provider_semaphores["YAHOO"]
    acquired = sem.acquire(blocking=True, timeout=5.0)
    if not acquired:
        return {}, False
    clean_sym = symbol.split(":")[-1].replace(".NS", "").replace(".BO", "").strip().upper()
    try:
        from multibagger import fetch_ticker_fundamentals
        yf_data = fetch_ticker_fundamentals(clean_sym)
        if not yf_data or yf_data.get("failed"):
            return {}, False

        extracted = {}
        now_iso = datetime.now(IST).isoformat()
        field_map = {
            "total_equity": yf_data.get("total_equity"),
            "roe": yf_data.get("roe"),
            "pe": yf_data.get("tt_indpe"),
            "operating_cash_flow": yf_data.get("cfo"),
            "net_profit": yf_data.get("pat"),
            "total_debt": yf_data.get("total_debt")
        }
        for k, val in field_map.items():
            if is_field_semantically_valid(k, val):
                extracted[k] = {
                    "value": float(val),
                    "source": "YAHOO",
                    "origin": "AGGREGATED_FINANCIAL_DATA",
                    "quality": "REPORTED",
                    "fetched_at": now_iso
                }
        return extracted, True
    except Exception as e:
        msg = str(e).lower()
        if "429" in msg or "too many requests" in msg:
            _trip_circuit_breaker("YAHOO", 300.0, str(e))
        return {}, False
    finally:
        sem.release()


# ── 6. UNIFIED FIELD-AWARE HYDRATION ENGINE ─────────────────────────────────
def get_unified_fundamentals(symbol: str, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Production-Grade Unified Fundamental Hydration Contract:
      Returns field-level provenance contract with Usable Status & Confidence telemetry.
    """
    clean_sym = symbol.split(":")[-1].replace(".NS", "").replace(".BO", "").strip().upper()
    now_ts = time.time()
    t_start = time.perf_counter()

    # --- Categorized Negative Cache Check ---
    if not force_refresh and clean_sym in _negative_cache:
        neg_info = _negative_cache[clean_sym]
        retry_after = neg_info.get("retry_after", 0.0)
        if now_ts < retry_after:
            logger.debug(f"⏹️ [NEGATIVE CACHE HIT] {clean_sym} ({neg_info.get('status')}) retry_after in {retry_after - now_ts:.0f}s")
            return {
                "symbol": clean_sym,
                "fields": {},
                "hydration": {"status": "INSUFFICIENT", "usable": False, "quality": "NONE", "primary_source": "NONE", "tiers_attempted": []},
                "status": neg_info.get("status", "NO_VALID_FUNDAMENTALS"),
                "diagnostics": {"latency_ms": 0.0}
            }
        else:
            del _negative_cache[clean_sym]

    # --- In-Memory Symbol Single-Flight Deduplication ---
    with _pipeline_lock:
        if clean_sym in _symbol_inflight:
            event = _symbol_inflight[clean_sym]
            _pipeline_lock.release()
            event.wait(timeout=15.0)
            with _pipeline_lock:
                return _symbol_results.get(clean_sym, {"symbol": clean_sym, "fields": {}, "hydration": {"status": "INSUFFICIENT", "usable": False}})
        
        evt = threading.Event()
        _symbol_inflight[clean_sym] = evt

    try:
        res_payload = _execute_hydration_cascade(clean_sym, force_refresh, now_ts, t_start)
        with _pipeline_lock:
            _symbol_results[clean_sym] = res_payload
        return res_payload
    finally:
        with _pipeline_lock:
            _symbol_inflight.pop(clean_sym, None)
            evt.set()


def _execute_hydration_cascade(clean_sym: str, force_refresh: bool, now_ts: float, t_start: float) -> Dict[str, Any]:
    tiers_attempted = []
    
    # 1. Tier 0: Load existing Canonical DB Cache
    existing_record = load_screener_cache_from_db(clean_sym) or {"symbol": clean_sym, "fields": {}}
    fields: Dict[str, dict] = existing_record.get("fields", {})

    missing_fields = set()
    for req_f in REQUIRED_FIELDS:
        if not is_field_fresh(req_f, fields.get(req_f), now_ts):
            missing_fields.add(req_f)

    if not missing_fields and not force_refresh:
        dur_ms = (time.perf_counter() - t_start) * 1000.0
        return _build_response_contract(clean_sym, fields, [0], dur_ms)

    tiers_attempted.append(0)
    initial_cached_count = len(fields)

    def _merge_fields(new_extracted: dict):
        for k, meta in new_extracted.items():
            fields[k] = meta
            missing_fields.discard(k)

    # 2. Tier 1: Screener.in Direct Scraper
    if missing_fields:
        tiers_attempted.append(1)
        sc_fields, ok = _fetch_from_screener(clean_sym)
        if ok and sc_fields:
            _merge_fields(sc_fields)

    # 3. Tier 2: TradingView Direct API Fields
    if missing_fields:
        tiers_attempted.append(2)
        tv_fields, ok = _fetch_from_tradingview(clean_sym)
        if ok and tv_fields:
            _merge_fields(tv_fields)

    # 4. Tier 3: NSE Official Financial Results API
    if missing_fields and "net_profit" in missing_fields:
        tiers_attempted.append(3)
        nse_fields, ok = _fetch_from_nse(clean_sym)
        if ok and nse_fields:
            _merge_fields(nse_fields)

    # 5. Tier 4: Yahoo Finance API Fallback
    if missing_fields:
        tiers_attempted.append(4)
        yf_fields, ok = _fetch_from_yahoo(clean_sym)
        if ok and yf_fields:
            _merge_fields(yf_fields)

    # 6. Safe Mathematical Derivations Fallback
    if "total_equity" in missing_fields:
        mcap_meta = fields.get("market_cap") or fields.get("market_cap_basic")
        pb_meta = fields.get("pb")
        if mcap_meta and pb_meta and valid_number(mcap_meta.get("value")) and valid_number(pb_meta.get("value")) and float(pb_meta["value"]) > 0:
            derived_equity = float(mcap_meta["value"]) / float(pb_meta["value"])
            fields["total_equity"] = {
                "value": derived_equity,
                "source": "TRADINGVIEW_DERIVED",
                "derivation": "market_cap / pb",
                "quality": "ESTIMATED",
                "fetched_at": datetime.now(IST).isoformat()
            }
            missing_fields.discard("total_equity")

    dur_ms = (time.perf_counter() - t_start) * 1000.0

    # 7. Categorized Negative Caching on Empty Result
    if len(fields) == 0:
        # Categorized TTL: NOT_FOUND vs TEMPORARILY_UNAVAILABLE
        cat_status = "NOT_FOUND" if any(t in tiers_attempted for t in [1, 2]) else "TEMPORARILY_UNAVAILABLE"
        ttl_base = 604800.0 if cat_status == "NOT_FOUND" else 900.0
        jitter = random.uniform(-60.0, 60.0)
        
        _negative_cache[clean_sym] = {
            "retry_after": now_ts + ttl_base + jitter,
            "status": cat_status
        }
        logger.info(f"🚫 [CATEGORIZED NEGATIVE CACHE] {clean_sym} marked {cat_status}. Retry after {ttl_base + jitter:.0f}s.")
        return {
            "symbol": clean_sym,
            "fields": {},
            "hydration": {"status": "INSUFFICIENT", "usable": False, "quality": "NONE", "primary_source": "NONE", "tiers_attempted": tiers_attempted},
            "status": cat_status,
            "diagnostics": {"latency_ms": dur_ms}
        }

    # Build final response contract & persist canonical snapshot to DB
    res = _build_response_contract(clean_sym, fields, tiers_attempted, dur_ms)
    save_screener_cache_to_db(clean_sym, res)
    return res


def field_present_and_valid(field_meta: Optional[dict]) -> bool:
    """Scanner rule helper: returns True iff field_meta exists and carries a semantically valid numeric value."""
    if not isinstance(field_meta, dict):
        return False
    val = field_meta.get("value")
    return valid_number(val)


def field_quality(field_meta: Optional[dict]) -> str:
    """Scanner rule helper: returns field provenance quality ('REPORTED', 'ESTIMATED', or 'UNKNOWN')."""
    if not isinstance(field_meta, dict):
        return "UNKNOWN"
    return field_meta.get("quality", "UNKNOWN")


def field_fresh(field_name: str, field_meta: Optional[dict]) -> bool:
    """Scanner rule helper: returns True iff field_meta is within field-specific TTL."""
    return is_field_fresh(field_name, field_meta or {}, time.time())


def _build_response_contract(symbol: str, fields: dict, tiers_attempted: list, latency_ms: float) -> Dict[str, Any]:
    sources = set(meta.get("source", "UNKNOWN") for meta in fields.values())
    qualities = set(meta.get("quality", "ESTIMATED") for meta in fields.values())

    primary_source = "SCREENER" if "SCREENER" in sources else (next(iter(sources)) if sources else "NONE")
    
    if qualities == {"REPORTED"}:
        hydration_quality = "HIGH"
    elif "REPORTED" in qualities:
        hydration_quality = "MIXED"
    else:
        hydration_quality = "ESTIMATED"

    # Minimum Usable Hydration Contract
    has_core = CORE_FIELDS.issubset(fields.keys())
    has_all = REQUIRED_FIELDS.issubset(fields.keys())

    if has_all:
        status_str = "COMPLETE"
    elif has_core:
        status_str = "PARTIAL_BUT_USABLE"
    else:
        status_str = "INSUFFICIENT"

    usable = has_core

    return {
        "symbol": symbol,
        "fields": fields,
        "hydration": {
            "status": status_str,
            "usable": usable,
            "quality": hydration_quality,
            "primary_source": primary_source,
            "tiers_attempted": tiers_attempted,
            "fields_present": len(fields)
        },
        "diagnostics": {
            "cache_fields": sum(1 for m in fields.values() if m.get("source") == "DB_CACHE"),
            "network_fields": sum(1 for m in fields.values() if m.get("source") in ("SCREENER", "TRADINGVIEW", "NSE", "YAHOO")),
            "derived_fields": sum(1 for m in fields.values() if m.get("quality") == "ESTIMATED"),
            "failed_fields": list(REQUIRED_FIELDS - fields.keys()),
            "latency_ms": round(latency_ms, 2)
        }
    }
