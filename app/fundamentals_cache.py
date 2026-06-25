import os
import json
import logging
import pandas as pd
# Ensure tzcache writable location before importing yfinance (robust import to support different cwd)
try:
    import app.yf_bootstrap
except Exception:
    try:
        import yf_bootstrap
    except Exception:
        pass
import yfinance as yf
from datetime import datetime, date
from zoneinfo import ZoneInfo
import concurrent.futures
from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit, CircuitOpenError

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

CACHE_FILE = "data/fundamentals_cache.json"

FUNDAMENTAL_REFRESH_SCHEDULE = {
    "NIFTY_500":     7,    # days
    "NIFTY_MIDCAP":  14,   # days
    "SMALLCAP_TAIL": 30,   # days
}

def load_cache() -> dict:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_cache(cache_data: dict):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache_data, f, indent=2)

def compute_piotroski(ticker_info: dict, financials: pd.DataFrame) -> int:
    try:
        score = 0
        if len(financials.columns) < 2:
            return -1 # Need at least 2 years

        # Profitability (4 pts)
        net_income = financials.loc["Net Income"] if "Net Income" in financials.index else pd.Series([0, 0])
        total_assets = financials.loc["Total Assets"] if "Total Assets" in financials.index else pd.Series([1, 1])
        
        score += 1 if net_income.iloc[0] > 0 else 0
        score += 1 if net_income.iloc[0] > net_income.iloc[1] else 0
        score += 1 if (net_income.iloc[0] / total_assets.iloc[0]) > 0 else 0
        score += 1 if ticker_info.get("operatingCashflow", 0) > 0 else 0
        
        # Leverage / Liquidity (3 pts)
        lt_debt = financials.loc["Long Term Debt"] if "Long Term Debt" in financials.index else pd.Series([0, 0])
        shares = financials.loc["Ordinary Shares Number"] if "Ordinary Shares Number" in financials.index else pd.Series([1, 1])
        
        score += 1 if lt_debt.iloc[0] < lt_debt.iloc[1] else 0
        score += 1 if ticker_info.get("currentRatio", 0) > ticker_info.get("previousCurrentRatio", 0) else 0
        score += 1 if shares.iloc[0] <= shares.iloc[1] else 0
        
        # Efficiency (2 pts)
        revenue = financials.loc["Total Revenue"] if "Total Revenue" in financials.index else pd.Series([0, 0])
        
        score += 1 if ticker_info.get("grossMargins", 0) > ticker_info.get("prevGrossMargins", 0) else 0
        score += 1 if (revenue.iloc[0] / total_assets.iloc[0]) > (revenue.iloc[1] / total_assets.iloc[1]) else 0
        
        return score
    except Exception as e:
        return -1


def fetch_single_piotroski(symbol: str) -> dict:
    try:
        yf_sym = f"{symbol.replace('_', '-')}.NS"
        try:
            yf_acquire()
            try:
                t = yf.Ticker(yf_sym)
                info = t.info
                # Combine financials and balance sheet to have all required rows
                fin = t.financials
                bs = t.balance_sheet
            finally:
                yf_release()
        except CircuitOpenError as ce:
            logger.error(f"YFinance circuit open; abort fundamentals fetch for {yf_sym}: {ce}")
            return {"score": -1, "date": str(datetime.now(IST).date())}
        except Exception as e:
            msg = str(e).lower()
            if 'too many requests' in msg or 'rate limit' in msg:
                record_rate_limit()
            return {"score": -1, "date": str(datetime.now(IST).date())}
        if fin.empty and bs.empty:
            return {"score": -1, "date": str(datetime.now(IST).date())}
            
        combined = pd.concat([fin, bs])
        score = compute_piotroski(info, combined)
        
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

        return {
            "score": score, 
            "date": str(datetime.now(IST).date()),
            "cfo_pat_ratio": cfo_pat_ratio,
            "retention_ratio": retention_ratio,
            "insider_hold": insider_hold,
            "forensic_flags": forensic_flags
        }
    except Exception as e:
        return {"score": -1, "date": str(datetime.now(IST).date())}


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
        return days_old > FUNDAMENTAL_REFRESH_SCHEDULE.get(tier, 30)
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
        return sym, fetch_single_piotroski(sym)
        
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(process, sym) for sym in to_fetch]
        for idx, future in enumerate(concurrent.futures.as_completed(futures)):
            sym, result = future.result()
            cache[sym] = result
            if idx % 50 == 0:
                logger.info(f"   Fetched {idx}/{len(to_fetch)} fundamentals")
                save_cache(cache)
                
    save_cache(cache)
    logger.info("✅ Fundamental fetch complete.")

def get_piotroski_score(symbol: str) -> int:
    cache = load_cache()
    entry = cache.get(symbol, {})
    return entry.get("score", -1)

def get_fundamentals(symbol: str) -> dict:
    cache = load_cache()
    return cache.get(symbol, {})
