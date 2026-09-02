import os
import json
import logging
import pandas as pd
# Ensure tzcache writable location before importing yfinance (robust import to support different cwd)
try:
    import yf_bootstrap
except Exception:
    pass
import yfinance as yf
from datetime import datetime, date
from zoneinfo import ZoneInfo
import concurrent.futures
from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit, record_success, CircuitOpenError

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

CACHE_FILE = "data/fundamentals_cache.json"
_IN_MEMORY_FUNDAMENTALS_CACHE = None
_IN_MEMORY_FUNDAMENTALS_MTIME = 0.0

FUNDAMENTAL_REFRESH_SCHEDULE = {
    "NIFTY_500":     7,    # days
    "NIFTY_MIDCAP":  14,   # days
    "SMALLCAP_TAIL": 30,   # days
}

def load_cache() -> dict:
    # [RULE 67 CHANGE-RATIONALE]:
    # Memoizes loaded fundamentals cache in RAM and syncs with DatasetRegistry.
    # Prevents repeated multi-megabyte JSON file disk reads on consecutive get_fundamentals() lookups in request loops.
    global _IN_MEMORY_FUNDAMENTALS_CACHE, _IN_MEMORY_FUNDAMENTALS_MTIME
    try:
        from data_registry import registry
        cached_reg = registry.get("fundamentals_cache")
        if isinstance(cached_reg, dict) and cached_reg:
            _IN_MEMORY_FUNDAMENTALS_CACHE = cached_reg
            return cached_reg
    except Exception:
        pass

    if not os.path.exists(CACHE_FILE):
        try:
            from database import download_parquet_from_db
            if download_parquet_from_db("fundamentals_cache", CACHE_FILE):
                logger.info("☁️ [CACHE] Restored fundamentals_cache from Postgres DB")
        except Exception as e:
            logger.warning(f"⚠️ Failed to restore fundamentals cache from DB: {e}")
            
    if os.path.exists(CACHE_FILE):
        try:
            mtime = os.path.getmtime(CACHE_FILE)
            if _IN_MEMORY_FUNDAMENTALS_CACHE is not None and mtime <= _IN_MEMORY_FUNDAMENTALS_MTIME:
                return _IN_MEMORY_FUNDAMENTALS_CACHE
            with open(CACHE_FILE) as f:
                data = json.load(f)
                if isinstance(data, dict):
                    _IN_MEMORY_FUNDAMENTALS_CACHE = data
                    _IN_MEMORY_FUNDAMENTALS_MTIME = mtime
                    try:
                        from data_registry import registry
                        registry.put("fundamentals_cache", data)
                    except Exception:
                        pass
                    return data
        except Exception:
            pass
    return _IN_MEMORY_FUNDAMENTALS_CACHE or {}

def save_cache(cache_data: dict, upload_to_db=False):
    # [RULE 67] Strip any None (null) values to prevent cache poisoning.
    # Only non-None values are written to disk to avoid key-exists-but-None
    # false positives in get_cached_fundamentals() validity checks.
    global _IN_MEMORY_FUNDAMENTALS_CACHE, _IN_MEMORY_FUNDAMENTALS_MTIME
    clean_cache = {k: v for k, v in cache_data.items() if v is not None}
    _IN_MEMORY_FUNDAMENTALS_CACHE = clean_cache
    _IN_MEMORY_FUNDAMENTALS_MTIME = time.time() if "time" in globals() else datetime.now().timestamp()
    try:
        from data_registry import registry
        registry.put("fundamentals_cache", clean_cache)
    except Exception:
        pass
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    # Write local file atomically first — local file is the authoritative source of truth.
    tmp_path = CACHE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(clean_cache, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, CACHE_FILE)

    if upload_to_db:
        # [RULE 67] DB upload is durable eventual persistence, NOT synchronous.
        # Previously: upload_parquet_to_db() blocked the caller for 1-3s per chunk-save.
        # Now: enqueue_durable_upload() returns immediately; background daemon retries
        # on failure with exponential backoff and recovers across process restarts.
        try:
            from durable_upload_queue import enqueue_durable_upload
            enqueue_durable_upload("fundamentals_cache", CACHE_FILE)
        except Exception as e:
            logger.warning(f"⚠️ Failed to enqueue fundamentals_cache DB upload: {e}")

def init_fundamentals_registry():
    cache = load_cache()
    from data_registry import registry
    registry.put("fundamentals_cache", cache)
    logger.info("✅ Fundamentals cache loaded into DatasetRegistry (DURABLE)")

def compute_piotroski(ticker_info: dict, financials: pd.DataFrame, balance_sheet: pd.DataFrame = None) -> int:
    """
    [VERSION: PIOTROSKI_FIX_v2.0] Fixed Piotroski computation.
    Accepts fin and bs separately to avoid NaN poisoning from pd.concat column misalignment.
    When fin/bs have different fiscal year date columns, pd.concat produces NaN rows that
    silently score 0 on almost every criterion, returning scores of 2-3 instead of 7-8.
    """
    try:
        score = 0
        # Use financials for income statement rows
        fin = financials
        # Use balance_sheet if provided, otherwise fall back to combined
        bs = balance_sheet if balance_sheet is not None else financials

        if fin is None or fin.empty or len(fin.columns) < 2:
            return -1  # Need at least 2 years of income data

        def safe_val(val, default=0.0) -> float:
            """Safely convert any float/int/Series/ndarray to a scalar float."""
            try:
                if hasattr(val, "values"):
                    v = val.values.flatten()
                    val = v[0] if len(v) > 0 else default
                if val is None or pd.isna(val):
                    return default
                return float(val)
            except Exception:
                return default

        def safe_row(df, row_name) -> list:
            """Extract a row from a DataFrame as a list of scalar floats."""
            if df is None or df.empty or row_name not in df.index:
                return []
            try:
                row_data = df.loc[row_name]
                if isinstance(row_data, pd.DataFrame):
                    row_data = row_data.iloc[0]
                values = [safe_val(x) for x in row_data if not pd.isna(x)]
                return values
            except Exception:
                return []

        # ── Profitability (4 pts) ──────────────────────────────────────
        net_income = safe_row(fin, "Net Income")
        total_assets = safe_row(bs, "Total Assets")

        # P1: Positive Net Income
        if len(net_income) >= 1:
            score += 1 if net_income[0] > 0 else 0

        # P2: Growing Net Income
        if len(net_income) >= 2:
            score += 1 if net_income[0] > net_income[1] else 0

        # P3: Positive ROA (NI/Assets)
        if len(net_income) >= 1 and len(total_assets) >= 1:
            ta = total_assets[0]
            if ta > 0:
                score += 1 if (net_income[0] / ta) > 0 else 0

        # P4: Positive Operating Cash Flow
        score += 1 if safe_val(ticker_info.get("operatingCashflow")) > 0 else 0

        # ── Leverage / Liquidity (3 pts) ───────────────────────────────
        lt_debt = safe_row(bs, "Long Term Debt")
        shares = safe_row(bs, "Ordinary Shares Number") or safe_row(fin, "Ordinary Shares Number")

        # L1: Lower Long-Term Debt
        if len(lt_debt) >= 2:
            score += 1 if lt_debt[0] < lt_debt[1] else 0

        # L2: Improved Current Ratio
        cur_ratio = safe_val(ticker_info.get("currentRatio"))
        prev_cur_ratio = safe_val(ticker_info.get("previousCurrentRatio"), cur_ratio - 0.01)
        score += 1 if cur_ratio > prev_cur_ratio else 0

        # L3: No Dilution (shares outstanding not increased)
        if len(shares) >= 2:
            score += 1 if shares[0] <= shares[1] else 0

        # ── Efficiency (2 pts) ─────────────────────────────────────────
        revenue = safe_row(fin, "Total Revenue")

        # E1: Improving Gross Margin
        gross_margin = safe_val(ticker_info.get("grossMargins"))
        prev_gross_margin = safe_val(ticker_info.get("prevGrossMargins"), gross_margin - 0.001)
        score += 1 if gross_margin > prev_gross_margin else 0

        # E2: Improving Asset Turnover (Revenue/Assets)
        if len(revenue) >= 2 and len(total_assets) >= 2:
            ta0 = total_assets[0]
            ta1 = total_assets[1]
            if ta0 > 0 and ta1 > 0:
                ato0 = revenue[0] / ta0
                ato1 = revenue[1] / ta1
                score += 1 if ato0 > ato1 else 0

        return score
    except Exception as e:
        logger.warning(f"compute_piotroski exception: {e}")
        return -1

def fetch_single_piotroski(symbol: str) -> dict:
    import time
    import random
    from bse_mapping_utils import load_bse_mappings, save_bse_mapping
    
    clean_sym = symbol.strip().upper()
    mappings = load_bse_mappings()
    if clean_sym in mappings:
        yf_sym = mappings[clean_sym]
    elif clean_sym.endswith(".NS") and clean_sym[:-3] in mappings:
        yf_sym = mappings[clean_sym[:-3]]
    else:
        yf_sym = f"{symbol.replace('_', '-')}.NS"
        
    def raw_yf_download(sym_name):
        t = yf.Ticker(sym_name)
        return t, t.info, t.financials, t.balance_sheet

    def try_fetch(sym_name):
        from yf_rate_limiter import safe_yf_call
        res = safe_yf_call(lambda: raw_yf_download(sym_name), symbol=sym_name, context="Piotroski Cache", max_retries=1)
        if res is None:
            return None, {}, pd.DataFrame(), pd.DataFrame()
        return res

    max_retries = 1
    t, info, fin, bs = None, None, None, None
    success = False
    
    for attempt in range(max_retries):
        try:
            time.sleep(random.uniform(1.2, 2.5))
            t, info, fin, bs = try_fetch(yf_sym)
            if fin.empty and bs.empty:
                if yf_sym.endswith(".NS"):
                    bse_sym = yf_sym[:-3] + ".BO"
                    logger.info(f"🔄 fundamentals: {yf_sym} empty, retrying with BSE {bse_sym}...")
                    t, info, fin, bs = try_fetch(bse_sym)
                    if not (fin.empty and bs.empty):
                        yf_sym = bse_sym
                        save_bse_mapping(symbol, bse_sym)
                        success = True
                        break
                elif yf_sym.endswith(".BO"):
                    logger.info(f"🗑️ fundamentals: Invalidating poisoned BSE mapping for {symbol} and retrying via NSE...")
                    try:
                        from bse_mapping_utils import invalidate_bse_mapping
                        clean_orig = symbol[:-3] if symbol.endswith(".NS") or symbol.endswith(".BO") else symbol
                        invalidate_bse_mapping(clean_orig)
                    except Exception as e:
                        logger.warning(f"Failed to invalidate mapping: {e}")
                    ns_sym = yf_sym[:-3] + ".NS"
                    t, info, fin, bs = try_fetch(ns_sym)
                    if not (fin.empty and bs.empty):
                        yf_sym = ns_sym
                        success = True
                        break
                # Rule: VAL-001
            if fin.empty and bs.empty:
                raise ValueError("Financials and Balance Sheet are both empty.")
            success = True
            break
        except Exception as e:
            msg = str(e).lower()
            is_rate_limit = 'too many requests' in msg or 'rate limit' in msg
            
            # If it's a rate limit, don't burn through alternate variants (they will just fail and trip the circuit).
            # Instead, skip the fallback logic and let the main retry loop backoff and try the same symbol again.
            if not is_rate_limit:
                retry_syms = []
                if yf_sym.endswith(".NS"):
                    retry_syms.append(yf_sym[:-3] + ".BO")
                if "-" in yf_sym:
                    retry_syms.append(yf_sym.replace("-", "&"))
                    if yf_sym.endswith(".NS"):
                        retry_syms.append(yf_sym.replace("-", "&")[:-3] + ".BO")
    
                for alt_sym in retry_syms:
                    logger.info(f"🔄 fundamentals exception for {yf_sym}, retrying with alt {alt_sym}...")
                    try:
                        t, info, fin, bs = try_fetch(alt_sym)
                        if not (fin.empty and bs.empty):
                            yf_sym = alt_sym
                            if alt_sym.endswith(".BO"):
                                save_bse_mapping(symbol, alt_sym)
                            success = True
                            break
                    except Exception:
                        pass
            
            # --- LAST RESORT FALLBACK via Yahoo Search API ---
            if not success and not is_rate_limit:
                import requests
                clean_base = symbol.strip().replace('.NS','').replace('.BO','')
                logger.info(f"🔍 Both standard NS/BO failed for {clean_base}. Trying Yahoo Search API...")
                try:
                    url = f"https://query2.finance.yahoo.com/v1/finance/search?q={clean_base}&quotesCount=3&country=India"
                    r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
                    if r.status_code == 200:
                        quotes = r.json().get('quotes', [])
                        for q in quotes:
                            s = q.get('symbol', '')
                            if s.endswith('.NS') or s.endswith('.BO') or s.endswith('.BSE'):
                                # [FIX] -SM.NS / -SM.BO = SME board stock.
                                # SME stocks structurally have NO financials on Yahoo Finance.
                                # Skip immediately — retrying wastes 30s+ and always fails.
                                if '-SM.' in s or s.endswith('-SM'):
                                    logger.info(f"⏭️ Skipping {s} — SME board stock (no financials available on Yahoo Finance). Marking as no_data.")
                                    return {"score": -1, "date": str(datetime.now(IST).date()), "failed": True, "no_data": True}
                                logger.info(f"🔍 Search API found: {s}")
                                t, info, fin, bs = try_fetch(s)
                                if not (fin.empty and bs.empty):
                                    yf_sym = s
                                    success = True
                                    break
                except Exception as search_err:
                    logger.debug(f"Search API fallback failed: {search_err}")

            # [FIX] If all NS/BO/Search variants failed on this first attempt,
            # there is no point retrying — mark as no_data and exit immediately.
            if not success and attempt == 0:
                logger.warning(f"⏭️ {yf_sym}: All symbol variants exhausted — no financials found anywhere. Fast-failing (no retry).")
                return {"score": -1, "date": str(datetime.now(IST).date()), "failed": True, "no_data": True}
            
            if success:
                break
                
            if attempt < max_retries - 1:
                backoff = 5 * (2 ** attempt) + random.uniform(0, 2)
                logger.warning(f"⚠️ {yf_sym}: Fetch failed on attempt {attempt+1}/{max_retries} due to {e}. Retrying in {backoff:.1f}s...")
                time.sleep(backoff)
            else:
                logger.warning(f"❌ {yf_sym}: Fundamentals fetch completely failed after {max_retries} attempts.")
                return {"score": -1, "date": str(datetime.now(IST).date()), "failed": True}

    if not success or (fin.empty and bs.empty):
        logger.warning(f"⚠️ {yf_sym}: Financials and Balance Sheet are both empty.")
        return {"score": -1, "date": str(datetime.now(IST).date()), "failed": True}
        
    # [VERSION: PIOTROSKI_FIX_v2.0] Pass fin and bs separately to compute_piotroski
    # Previously: pd.concat([fin, bs]) caused NaN misalignment when fiscal year columns differ,
    # resulting in incorrect low scores (3/9) for stocks that actually score 8/9.
    score = compute_piotroski(info, fin, balance_sheet=bs)
    record_success()
        
    # Multi-bagger enhancements extraction
    ocf = info.get("operatingCashflow")
    net_income = info.get("netIncomeToCommon")
    if net_income is None:
        try:
            net_income = fin.loc["Net Income"].iloc[0] if "Net Income" in fin.index else None
        except Exception:
            net_income = None
            
    # Calculate CFO/PAT ratio
    cfo_pat_ratio = None
    if ocf is not None and net_income is not None and net_income > 0:
        cfo_pat_ratio = ocf / net_income
        
    # Dividends / Retention
    payout_ratio = info.get("payoutRatio")
    retention_ratio = 1.0 - payout_ratio if payout_ratio is not None else None
    
    # Insider holding
    insider_hold = info.get("heldPercentInsiders")
    
    # Valuation Fallbacks (for when TradingView returns NaN)
    pb_fallback = info.get("priceToBook")
    pe_fallback = info.get("trailingPE")
    
    # Forensics (Asset Quality & Accruals)
    forensic_flags = 0
    try:
        revenue = fin.loc["Total Revenue"] if "Total Revenue" in fin.index else None
        total_assets = bs.loc["Total Assets"] if "Total Assets" in bs.index else None
        
        if revenue is not None and len(revenue) >= 2 and total_assets is not None and len(total_assets) >= 2:
            rev_growth = (revenue.iloc[0] - revenue.iloc[1]) / abs(revenue.iloc[1]) if revenue.iloc[1] != 0 else 0
            asset_growth = (total_assets.iloc[0] - total_assets.iloc[1]) / abs(total_assets.iloc[1]) if total_assets.iloc[1] != 0 else 0
            if asset_growth > 0 and rev_growth > 0 and asset_growth > (2.0 * rev_growth):
                forensic_flags += 1
                
        if net_income is not None and ocf is not None and revenue is not None and len(revenue) >= 1:
            rev_curr = revenue.iloc[0]
            if rev_curr > 0:
                if (net_income - ocf) > (0.1 * rev_curr):
                    forensic_flags += 1
    except Exception:
        pass

    # ROE / ROCE / Debt-to-Equity extraction for Wealth Engine
    roe = None
    if info.get("returnOnEquity") is not None:
        roe = float(info.get("returnOnEquity")) * 100.0

    roce = None
    roa = info.get("returnOnAssets")
    if roa is not None:
        roce = float(roa) * 100.0 * 1.35
    elif roe is not None:
        roce = roe * 0.95

    debt_equity = None
    if info.get("debtToEquity") is not None:
        debt_equity = float(info.get("debtToEquity")) / 100.0

    return {
        "score": score, 
        "date": str(datetime.now(IST).date()),
        "cfo_pat_ratio": cfo_pat_ratio,
        "retention_ratio": retention_ratio,
        "insider_hold": insider_hold,
        "forensic_flags": forensic_flags,
        "pb_fallback": pb_fallback,
        "pe_fallback": pe_fallback,
        "roe": roe,
        "roce": roce,
        "debt_equity": debt_equity
    }


def get_tier(market_cap_cr: float) -> str:
    if market_cap_cr >= 20000:
        return "NIFTY_500"
    elif market_cap_cr >= 5000:
        return "NIFTY_MIDCAP"
    else:
        return "SMALLCAP_TAIL"

def is_stale(cache_entry: dict, tier: str) -> bool:
    if not cache_entry:
        return True
    try:
        entry_date = datetime.strptime(cache_entry["date"], "%Y-%m-%d").date()
        days_old = (datetime.now(IST).date() - entry_date).days
        ttl_limit = FUNDAMENTAL_REFRESH_SCHEDULE.get(tier, 30)
        
        # If it failed to fetch (no data), retry on the next run after 2 days (48 hour cooldown)
        if cache_entry.get("failed", False):
            stale = days_old > 2
            if stale:
                logger.info(f"🔄 [FUNDAMENTALS DB CACHE] Failed entry STALE ({days_old}d > 2d cooldown). Refetching...")
            return stale

        stale = days_old > ttl_limit
        if stale:
            logger.info(f"🔄 [FUNDAMENTALS DB CACHE] Entry STALE ({days_old}d > {ttl_limit}d TTL for {tier}). Refetching...")
        return stale
    except Exception:
        return True


def compute_tradingview_health_score(row: pd.Series) -> int:
    """
    Compute a 7-point fundamental health score derived from TradingView live financial metrics:
    1. ROE > 0 (+1)
    2. ROA > 0 (+1)
    3. ROE >= 12% (+1)
    4. Debt/Equity <= 1.0 (+1)
    5. Debt/Equity <= 0.5 (+1)
    6. Gross Margin >= 20% (+1)
    7. Operating Margin >= 10% (+1)
    """
    score = 0
    roe = row.get("return_on_equity_fy")
    roa = row.get("return_on_assets_fq")
    de = row.get("debt_to_equity_fy")
    gm = row.get("gross_margin_ttm")
    om = row.get("operating_margin_ttm")
    
    if pd.isna(roe) and pd.isna(roa) and pd.isna(de) and pd.isna(gm) and pd.isna(om):
        return -1
        
    score = 0
    if pd.notna(roe) and roe > 0:
        score += 1
    if pd.notna(roa) and roa > 0:
        score += 1
    if pd.notna(roe) and roe >= 12.0:
        score += 1
    if pd.notna(de) and de <= 1.0:
        score += 1
    if pd.notna(de) and de <= 0.5:
        score += 1
    if pd.notna(gm) and gm >= 20.0:
        score += 1
    if pd.notna(om) and om >= 10.0:
        score += 1
    return score


def fetch_tradingview_fundamentals_bulk() -> dict:
    """
    Fetch fundamental metrics for the entire market universe using TradingView Screener API.
    Returns dict mapping symbol -> fundamental dict in <3 seconds with zero rate limits.
    """
    logger.info("⚡ [FUNDAMENTALS] Fetching TradingView bulk universe fundamentals...")
    try:
        from valuation_utils import fetch_full_universe_for_valuation
        df = fetch_full_universe_for_valuation()
        if df is None or df.empty:
            logger.warning("⚠️ TradingView bulk universe empty.")
            return {}
        
        tv_dict = {}
        today_str = str(datetime.now(IST).date())
        
        for _, row in df.iterrows():
            sym_raw = row.get("name") or row.get("ticker", "")
            if not sym_raw:
                continue
            clean_sym = sym_raw.split(":")[-1].replace("-", "_").upper().strip()
            
            roe = float(row["return_on_equity_fy"]) if pd.notna(row.get("return_on_equity_fy")) else None
            roa = float(row["return_on_assets_fq"]) if pd.notna(row.get("return_on_assets_fq")) else None
            de = float(row["debt_to_equity_fy"]) if pd.notna(row.get("debt_to_equity_fy")) else None
            pe = float(row["price_earnings_ttm"]) if pd.notna(row.get("price_earnings_ttm")) else None
            pb = float(row["price_book_ratio"]) if pd.notna(row.get("price_book_ratio")) else None
            
            tv_health_score = compute_tradingview_health_score(row)
            roce = (roa * 1.35) if roa is not None else ((roe * 0.95) if roe is not None else None)
            
            mcap = float(row.get("market_cap_basic")) if pd.notna(row.get("market_cap_basic")) else None
            rev_growth = float(row.get("total_revenue_yoy_growth_ttm")) / 100.0 if pd.notna(row.get("total_revenue_yoy_growth_ttm")) else None
            
            # [RULE 67] Extract operating_margin_ttm as OCF proxy for Pass 1 scoring.
            # Gate 3 amendment: operating_margin_ttm is already fetched by
            # fetch_full_universe_for_valuation() — capture it here so PASS1_SCORE
            # can apply the operating-cash-flow gate without a YFinance round-trip.
            # NOTE: This is a proxy only. Pass 2 computes true OCF from YFinance balance sheet.
            op_margin = float(row["operating_margin_ttm"]) if pd.notna(row.get("operating_margin_ttm")) else None

            entry = {
                "score": tv_health_score,
                "date": today_str,
                "roe": roe,
                "roce": roce,
                "debt_equity": de,
                "pb_fallback": pb,
                "pe_fallback": pe,
                "market_cap": mcap,
                "revenue_cagr_3y": rev_growth,
                # [Gate 3] OCF proxy from TradingView operating margin (TTM)
                # Used in PASS1_SCORE only. True YFinance operating_cash_flow_ttm
                # is fetched in Pass 2 for finalists.
                "operating_margin_ttm": op_margin,
                "data_freshness": "LIVE",
                "source": "TRADINGVIEW_BULK",
                "cache_tier": "TV_BASELINE",
                "failed": False
            }
            tv_dict[clean_sym] = entry
            if clean_sym != sym_raw:
                tv_dict[sym_raw] = entry
                
        logger.info(f"✅ [FUNDAMENTALS] Successfully loaded bulk fundamentals for {len(tv_dict)} symbols via TradingView")
        return tv_dict
    except Exception as e:
        logger.warning(f"⚠️ TradingView bulk fundamental fetch failed: {e}")
        return {}


def refresh_fundamentals_tiered(universe_df: pd.DataFrame):
    logger.info("🔄 Refreshing Fundamentals (TradingView Bulk + Throttled Fallback)...")
    cache = load_cache()
    
    # Step 1: Instant Bulk Update via TradingView Screener API (<3s)
    tv_data = fetch_tradingview_fundamentals_bulk()
    updated_from_tv = 0
    if tv_data:
        for _, row in universe_df.iterrows():
            sym = row["name"]
            clean_sym = sym.replace("-", "_").upper().strip()
            tv_entry = tv_data.get(clean_sym) or tv_data.get(sym)
            if tv_entry:
                existing = cache.get(sym) or {}
                tier = get_tier(row.get("market_cap_basic", 0) / 10000000)
                
                # Preserve exact YFinance Piotroski score if already present and fresh (< 7/14/30 days)
                if existing.get("source") == "YFINANCE":
                    yf_dt = existing.get("yf_date") or existing.get("date")
                    tv_entry["yf_date"] = yf_dt
                    if not is_stale({"date": yf_dt}, tier):
                        tv_entry["score"] = existing.get("score", tv_entry["score"])
                        tv_entry["source"] = "YFINANCE"
                
                cache[sym] = tv_entry
                updated_from_tv += 1
        logger.info(f"⚡ [FUNDAMENTALS] Primary TradingView bulk refresh updated {updated_from_tv}/{len(universe_df)} stocks instantly.")
    
    # Step 2: Throttled Secondary Fallback for stocks missing fundamental scores (-1)
    # TradingView Bulk (Step 1) provides live fundamental health metrics for 98%+ of the universe in <3s.
    # Only queue YFinance secondary balance sheet requests for stocks where fundamental data is missing.
    to_fetch_yf = []
    for _, row in universe_df.iterrows():
        sym = row["name"]
        mc = row.get("market_cap_basic", 0) / 10000000
        tier = get_tier(mc)
        entry = cache.get(sym) or {}
        yf_dt = entry.get("yf_date") or entry.get("date")
        # Only fetch YFinance if there is no valid fundamental score or entry is stale failed
        if not entry or entry.get("score", -1) == -1 or entry.get("failed", False):
            if is_stale({"date": yf_dt}, tier):
                to_fetch_yf.append(sym)
            
    logger.info(f"📊 [FUNDAMENTALS] Secondary YFinance queue for full Piotroski balance sheets: {len(to_fetch_yf)} symbols pending (limiting to top 10 per run)")
    
    if to_fetch_yf:
        # Take at most 10 symbols per run to keep Yahoo Finance calls light and safe
        yf_batch = to_fetch_yf[:10]
        missing_data_stocks = []
        for idx, sym in enumerate(yf_batch):
            try:
                import time
                time.sleep(2.5) # Gentle 2.5s spacing between YFinance calls
                result = fetch_single_piotroski(sym)
                if result is not None:
                    result["source"] = "YFINANCE"
                    cache[sym] = result
                    score_val = result.get("score", -1)
                    logger.info(f"🔄 [FUNDAMENTALS] [{idx+1}/{len(yf_batch)}] Fetched YFinance Piotroski for {sym} | Score={score_val}")
                    if result.get("failed", False):
                        missing_data_stocks.append(sym)
            except Exception as yf_err:
                logger.warning(f"⚠️ YFinance fallback failed for {sym}: {yf_err}")

    save_cache(cache, upload_to_db=True)
    
    from data_registry import registry
    registry.put("fundamentals_cache", cache)
    logger.info("✅ Fundamental refresh complete.")

def get_piotroski_score(symbol: str) -> int:
    f_dict = get_fundamentals(symbol)
    return f_dict.get("score", -1)

_norm_cache_index = {}
_norm_cache_len = 0
_cache_stats = {"total_lookups": 0, "o1_hits": 0, "o1_misses": 0, "linear_scans": 0, "index_build_ms": 0.0}

def get_fundamentals_cache_stats() -> dict:
    """Returns telemetry statistics for fundamentals cache lookups."""
    return dict(_cache_stats)

def _get_normalized_cache_index(cache: dict) -> dict:
    global _norm_cache_index, _norm_cache_len, _cache_stats
    if _norm_cache_index and len(cache) == _norm_cache_len:
        return _norm_cache_index
    try:
        import time as _t_mod
        _t0 = _t_mod.perf_counter()
        from valuation_utils import normalize_id
        idx = {}
        for k, v in cache.items():
            if v and isinstance(v, dict):
                norm_k = normalize_id(k)
                if norm_k:
                    idx[norm_k] = v
        _norm_cache_index = idx
        _norm_cache_len = len(cache)
        _dur_ms = (_t_mod.perf_counter() - _t0) * 1000.0
        _cache_stats["index_build_ms"] = round(_dur_ms, 2)
        logger.info(f"⚡ [FUNDAMENTALS INDEX] Built O(1) normalized index for {len(_norm_cache_index)} entries in {_dur_ms:.2f}ms (memoized for cache size={len(cache)})")
        return _norm_cache_index
    except Exception as e:
        logger.warning(f"Failed to build normalized index: {e}")
        return {}

def get_fundamentals(symbol: str) -> dict:
    global _cache_stats
    _cache_stats["total_lookups"] += 1
    from data_registry import registry
    cache = registry.get("fundamentals_cache")
    if not cache:
        cache = load_cache()
    if not cache:
        _cache_stats["o1_misses"] += 1
        return {}
    res = cache.get(symbol)
    if res:
        _cache_stats["o1_hits"] += 1
        return res
    # [VERSION: O1_NORM_CACHE_FIX_v1.1] Instant O(1) normalized lookup with memoized index
    try:
        from valuation_utils import normalize_id
        norm_s = normalize_id(symbol)
        if norm_s:
            norm_idx = _get_normalized_cache_index(cache)
            found = norm_idx.get(norm_s)
            if found:
                _cache_stats["o1_hits"] += 1
                return found
    except Exception:
        pass
    _cache_stats["o1_misses"] += 1
    return {}
