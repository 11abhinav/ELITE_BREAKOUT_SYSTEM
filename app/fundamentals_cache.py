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

FUNDAMENTAL_REFRESH_SCHEDULE = {
    "NIFTY_500":     7,    # days
    "NIFTY_MIDCAP":  14,   # days
    "SMALLCAP_TAIL": 30,   # days
}

def load_cache() -> dict:
    if not os.path.exists(CACHE_FILE):
        try:
            from database import download_parquet_from_db
            if download_parquet_from_db("fundamentals_cache", CACHE_FILE):
                logger.info("☁️ [CACHE] Restored fundamentals_cache from Postgres DB")
        except Exception as e:
            logger.warning(f"⚠️ Failed to restore fundamentals cache from DB: {e}")
            
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_cache(cache_data: dict, upload_to_db=False):
    # Strip any None (null) values to prevent cache poisoning
    clean_cache = {k: v for k, v in cache_data.items() if v is not None}
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(clean_cache, f, indent=2)
        
    if upload_to_db:
        try:
            from database import upload_parquet_to_db
            upload_parquet_to_db("fundamentals_cache", CACHE_FILE)
            logger.info("☁️ [CACHE] Uploaded fundamentals_cache to Postgres DB")
        except Exception as e:
            logger.warning(f"⚠️ Failed to backup fundamentals cache to DB: {e}")

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
        
    def try_fetch(sym_name):
        try:
            yf_acquire(context=f"Piotroski Cache | {symbol}")
            try:
                t = yf.Ticker(sym_name)
                info = t.info
                fin = t.financials
                bs = t.balance_sheet
            finally:
                yf_release()
            return t, info, fin, bs
        except Exception as inner_e:
            msg = str(inner_e).lower()
            if 'too many requests' in msg or 'rate limit' in msg:
                record_rate_limit(context=f"Piotroski Cache | {symbol}")
            raise inner_e

    max_retries = 3
    t, info, fin, bs = None, None, None, None
    success = False
    
    for attempt in range(max_retries):
        try:
            # Increase base sleep from 0.5-2.0s to 1.5-3.5s to stay well below Yahoo Finance 2000 req/hr limits
            time.sleep(random.uniform(1.5, 3.5))
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


def refresh_fundamentals_tiered(universe_df: pd.DataFrame):
    logger.info("🔄 Refreshing Piotroski Fundamentals (Tiered)...")
    cache = load_cache()
    
    to_fetch = []
    for _, row in universe_df.iterrows():
        sym = row["name"]
        mc = row.get("market_cap_basic", 0) / 10000000
        tier = get_tier(mc)
        if is_stale(cache.get(sym), tier):
            to_fetch.append(sym)
            
    logger.info(f"📥 Need to fetch {len(to_fetch)} symbols out of {len(universe_df)} for Piotroski.")
    
    if not to_fetch:
        return
        
    def process(sym):
        import time
        time.sleep(0.1) # Yield CPU to Flask for health checks
        return sym, fetch_single_piotroski(sym)
        
    import gc
    missing_data_stocks = []
    
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            futures = [executor.submit(process, sym) for sym in to_fetch]
            for idx, future in enumerate(concurrent.futures.as_completed(futures, timeout=1800)):
                sym, result = future.result()
                
                # None means rate limited or circuit open -> skip caching so it's retried next time
                if result is not None:
                    cache[sym] = result
                    if result.get("failed", False):
                        missing_data_stocks.append(sym)
                        
                if idx > 0 and idx % 10 == 0:
                    logger.info(f"   Fetched {idx}/{len(to_fetch)} fundamentals")
                    save_cache(cache, upload_to_db=True)
                    gc.collect() # Force cleanup of Pandas DataFrames to avoid OOM
    except concurrent.futures.TimeoutError:
        logger.error("❌ Timeout fetching fundamentals in fundamentals_cache. Aborting remaining fetches to prevent deadlock.")
                
    save_cache(cache, upload_to_db=True)
    
    from data_registry import registry
    registry.put("fundamentals_cache", cache)
    
    # Notify Admin if any stocks permanently failed (No Data)
    if missing_data_stocks:
        try:
            from database import insert_notification
            msg = f"Yahoo Finance returned empty data for {len(missing_data_stocks)} stocks. These have been skipped and will be retried in 2 days.\nExamples: {', '.join(missing_data_stocks[:5])}"
            insert_notification("info", "⚠️ Yahoo Missing Fundamentals", msg)
        except Exception:
            pass
            
    logger.info("✅ Fundamental fetch complete.")

def get_piotroski_score(symbol: str) -> int:
    from data_registry import registry
    cache = registry.get("fundamentals_cache")
    if not cache:
        cache = load_cache() # Fallback if registry not initialized
    entry = cache.get(symbol) or {}
    return entry.get("score", -1)

def get_fundamentals(symbol: str) -> dict:
    from data_registry import registry
    cache = registry.get("fundamentals_cache")
    if not cache:
        cache = load_cache()
    return cache.get(symbol) or {}
