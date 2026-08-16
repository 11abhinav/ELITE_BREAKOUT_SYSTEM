import logging
import pandas as pd
import numpy as np
import time
import os
import requests
import threading

logger = logging.getLogger(__name__)

_thread_local = threading.local()

def get_tt_session():
    if not hasattr(_thread_local, "tt_session"):
        _thread_local.tt_session = requests.Session()
    return _thread_local.tt_session

UNIVERSE_CACHE_PATH = "data/tradingview_universe_cache.pkl"

def normalize_id(x: str) -> str:
    if pd.isna(x) or not isinstance(x, str): return ""
    if ":" in x: x = x.split(":")[-1]
    return x.upper().replace("-", "").replace("_", "").replace("&", "").strip()

def fetch_full_universe_for_valuation() -> pd.DataFrame:
    from tradingview_screener import Query, col
    fields = [
        "ticker", "name", "sector", "market_cap_basic", 
        "return_on_equity_fy", "return_on_assets_fq", "total_revenue_yoy_growth_ttm",
        "debt_to_equity_fy", "gross_margin_ttm", "operating_margin_ttm", "total_assets_fy",
        "price_earnings_ttm", "price_book_ratio",
        "enterprise_value_ebitda_ratio", "dividend_yield_recent"
    ]
    
    # Check local cache freshness first (72 hours TTL)
    def load_valid_cache():
        if os.path.exists(UNIVERSE_CACHE_PATH):
            try:
                df = pd.read_pickle(UNIVERSE_CACHE_PATH)
                gen_time = df.attrs.get("generated_at", 0.0)
                age_hours = (time.time() - gen_time) / 3600
                if age_hours < 72:
                    return df
            except Exception as e:
                logger.warning(f"Failed to read local universe cache: {e}")
        return None

    cached_df = load_valid_cache()
    if cached_df is not None:
        return cached_df

    # Not found or stale locally. Try downloading from database cache.
    logger.info("ℹ️ Local TradingView cache missing or stale. Restoring from database...")
    try:
        from database import download_parquet_from_db
        if download_parquet_from_db("tradingview_universe", UNIVERSE_CACHE_PATH):
            cached_df = load_valid_cache()
            if cached_df is not None:
                logger.info("✅ Successfully loaded fresh TradingView universe cache from database.")
                return cached_df
    except Exception as db_err:
        logger.warning(f"Failed to restore TradingView cache from database: {db_err}")
            
    for attempt in range(3):
        try:
            q = (
                Query()
                .set_markets("india")
                .select(*fields)
                .where(col("exchange") == "NSE")
                .order_by("ticker")
                .limit(5000)
            )
            # Pass explicit connect and read timeout down to requests.post
            total, df = q.get_scanner_data(timeout=(5, 15))
            
            if df is not None and not df.empty:
                # Paginate if needed
                pages = [df]
                current_len = len(df)
                while current_len < total:
                    offset = current_len
                    q_next = q.offset(offset)
                    next_total, next_df = q_next.get_scanner_data()
                    if next_df is None or next_df.empty:
                        break
                    pages.append(next_df)
                    current_len += len(next_df)
                
                df = pd.concat(pages, ignore_index=True)
                del pages
                
                if len(df) < total:
                    logger.warning(f"Universe pagination incomplete: fetched {len(df)} out of {total} total stocks.")
                
                # Remove duplicated columns (like 'ticker') returned by tradingview_screener
                df = df.loc[:, ~df.columns.duplicated()].copy()
                
                # Remove duplicate rows by ticker due to pagination overlap
                if "ticker" in df.columns:
                    df = df.drop_duplicates(subset=["ticker"], keep="first")
                
                # Add normalized columns for fast matching
                if "ticker" in df.columns:
                    df["ticker_norm"] = df["ticker"].apply(normalize_id)
                if "name" in df.columns:
                    df["name_norm"] = df["name"].apply(normalize_id)
                    
                # Coerce numeric fields to float
                numeric_cols = [
                    "market_cap_basic", "return_on_equity_fy", "return_on_assets_fq",
                    "debt_to_equity_fy", "gross_margin_ttm", "operating_margin_ttm", "total_assets_fy",
                    "total_revenue_yoy_growth_ttm", "price_earnings_ttm", "price_book_ratio",
                    "enterprise_value_ebitda_ratio", "dividend_yield_recent"
                ]
                for col_name in numeric_cols:
                    if col_name in df.columns:
                        df[col_name] = pd.to_numeric(df[col_name], errors="coerce")
                        
                if "return_on_equity_fy" in df.columns and "total_revenue_yoy_growth_ttm" in df.columns:
                    logger.info(f"Universe Refresh: ROE range [{df['return_on_equity_fy'].min():.1f}, {df['return_on_equity_fy'].max():.1f}], Growth range [{df['total_revenue_yoy_growth_ttm'].min():.1f}, {df['total_revenue_yoy_growth_ttm'].max():.1f}]")
                    
                # Save to cache atomically
                df.attrs["generated_at"] = time.time()
                os.makedirs(os.path.dirname(UNIVERSE_CACHE_PATH), exist_ok=True)
                temp_path = f"{UNIVERSE_CACHE_PATH}.tmp"
                with open(temp_path, "wb") as f:
                    df.to_pickle(f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, UNIVERSE_CACHE_PATH)

                # Upload to DB in background so we don't block
                try:
                    from database import upload_parquet_to_db
                    import threading
                    def upload_job():
                        t_name = threading.current_thread().name
                        logger.info(f"🚀 [BACKGROUND WORKER START] Worker='{t_name}' | InitiatedBy='TradingViewFetcher' | Action='Uploading tradingview_universe to DB parquet_cache'")
                        _t_start = time.perf_counter()
                        upload_parquet_to_db("tradingview_universe", UNIVERSE_CACHE_PATH)
                        dur_s = time.perf_counter() - _t_start
                        logger.info(f"✅ [BACKGROUND WORKER COMPLETE] Worker='{t_name}' | Action='Uploaded tradingview_universe to DB' | Duration={dur_s:.2f}s")

                    from database import submit_background_upload
                    submit_background_upload(upload_job)
                except Exception as up_err:
                    logger.warning(f"Failed to spawn background upload for TradingView universe: {up_err}")

                return df
        except Exception as e:
            logger.exception(f"Attempt {attempt + 1}: Failed to fetch market universe")
            time.sleep(2 ** attempt)
            
    # Final fallback: return whatever we have in the local cache file, regardless of age
    if os.path.exists(UNIVERSE_CACHE_PATH):
        try:
            df = pd.read_pickle(UNIVERSE_CACHE_PATH)
            gen_time = df.attrs.get("generated_at", 0.0)
            age_hours = (time.time() - gen_time) / 3600 if gen_time else 999.0
            logger.warning(f"⚠️ Loading stale market universe cache as final fallback ({age_hours:.1f} hours old).")
            return df
        except Exception as e:
            logger.exception(f"Failed to load stale universe cache: {e}")
            
    return pd.DataFrame()

_peer_medians_cache: dict = {"ts": 0.0, "data": {}}
_peer_medians_lock = threading.Lock()

def compute_peer_medians(symbols: list, known_sectors: dict = None) -> dict:
    """
    Compute median P/E, P/B, and ROE per stock dynamically using a peer subset from the overall market universe.
    Cached for 1 hour in-memory to eliminate overhead per scan.
    Returns {symbol: {"median_pe": ..., "median_pb": ..., "median_roe": ...}}
    """
    global _peer_medians_cache
    now = time.time()
    with _peer_medians_lock:
        if _peer_medians_cache["data"] and (now - _peer_medians_cache["ts"]) < 3600:
            cached = _peer_medians_cache["data"]
            missing = [s for s in symbols if s not in cached]
            if not missing:
                return {s: cached[s] for s in symbols if s in cached}
            elif len(missing) < 20 and len(cached) > 0:
                result = {s: cached[s] for s in symbols if s in cached}
                for ms in missing:
                    result[ms] = {"median_pe": None, "median_pb": None, "median_roe": None, "peer_count": 0}
                return result

    try:
        universe_df = fetch_full_universe_for_valuation()
    except Exception as e:
        logger.exception(f"Failed to fetch full market universe")
        universe_df = None

    medians_map = {}
    if universe_df is None or universe_df.empty:
        for symbol in symbols:
            medians_map[symbol] = {"median_pe": None, "median_pb": None, "median_roe": None, "peer_count": 0}
        return medians_map

    # 1. Pre-clean the universe ONCE (O(N) vectorized instead of O(N*M))
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # Ensure normalized columns
        if "ticker_norm" not in universe_df.columns and "ticker" in universe_df.columns:
            universe_df["ticker_norm"] = universe_df["ticker"].apply(normalize_id)
        if "name_norm" not in universe_df.columns and "name" in universe_df.columns:
            universe_df["name_norm"] = universe_df["name"].apply(normalize_id)

        # Pre-clean outlier values using vectorized np.where
        if "price_earnings_ttm" in universe_df.columns:
            universe_df["price_earnings_ttm"] = np.where(
                universe_df["price_earnings_ttm"].notna() & (universe_df["price_earnings_ttm"] > 0) & (universe_df["price_earnings_ttm"] < 500),
                universe_df["price_earnings_ttm"], np.nan
            )
        if "price_book_ratio" in universe_df.columns:
            universe_df["price_book_ratio"] = np.where(
                universe_df["price_book_ratio"].notna() & (universe_df["price_book_ratio"] > 0) & (universe_df["price_book_ratio"] < 100),
                universe_df["price_book_ratio"], np.nan
            )
        if "return_on_equity_fy" in universe_df.columns:
            universe_df["return_on_equity_fy"] = np.where(
                universe_df["return_on_equity_fy"].notna() & (universe_df["return_on_equity_fy"] > -200) & (universe_df["return_on_equity_fy"] < 200),
                universe_df["return_on_equity_fy"], np.nan
            )

    # 2. Pre-group by sector
    sectors_cache = {}
    if "sector" in universe_df.columns:
        sectors_cache = {sector: df for sector, df in universe_df.groupby("sector") if pd.notna(sector)}

    # 3. Create fast-lookup dictionaries (O(1) lookup vs O(N) boolean indexing)
    # We take the first match for each key
    ticker_norm_map = universe_df.drop_duplicates(subset=["ticker_norm"]).set_index("ticker_norm").to_dict(orient="index") if "ticker_norm" in universe_df.columns else {}
    name_norm_map = universe_df.drop_duplicates(subset=["name_norm"]).set_index("name_norm").to_dict(orient="index") if "name_norm" in universe_df.columns else {}
    name_map = universe_df.drop_duplicates(subset=["name"]).set_index("name").to_dict(orient="index") if "name" in universe_df.columns else {}

    for symbol in symbols:
        medians_map[symbol] = {"median_pe": None, "median_pb": None, "median_roe": None, "peer_count": 0}
        
        norm_sym = normalize_id(symbol)
        stock_row = None
        if norm_sym in ticker_norm_map:
            stock_row = ticker_norm_map[norm_sym]
        elif norm_sym in name_norm_map:
            stock_row = name_norm_map[norm_sym]
        elif symbol in name_map:
            stock_row = name_map[symbol]
            
        sector = stock_row.get("sector") if stock_row else (known_sectors.get(symbol) if known_sectors else None)
        if not sector or pd.isna(sector) or sector not in sectors_cache:
            continue
            
        peers = sectors_cache[sector]
        if stock_row and "ticker" in stock_row:
            current_ticker = stock_row["ticker"]
            peers = peers[peers["ticker"] != current_ticker]
            
        mcap = stock_row.get("market_cap_basic") if stock_row else None
        roe = stock_row.get("return_on_equity_fy") if stock_row else None
        growth = stock_row.get("total_revenue_yoy_growth_ttm") if stock_row else None
        
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            raw_pe = peers["price_earnings_ttm"].median() if "price_earnings_ttm" in peers.columns else np.nan
            raw_pb = peers["price_book_ratio"].median() if "price_book_ratio" in peers.columns else np.nan
            raw_roe = peers["return_on_equity_fy"].median() if "return_on_equity_fy" in peers.columns else np.nan
            
            if pd.isna(mcap) or pd.isna(roe) or pd.isna(growth):
                valid_pe_count = int(peers["price_earnings_ttm"].count()) if "price_earnings_ttm" in peers.columns else 0
                valid_pb_count = int(peers["price_book_ratio"].count()) if "price_book_ratio" in peers.columns else 0
                valid_roe_count = int(peers["return_on_equity_fy"].count()) if "return_on_equity_fy" in peers.columns else 0
                conservative_peer_count = min(valid_pe_count, valid_pb_count, valid_roe_count)
                medians_map[symbol].update({
                    "median_pe": float(raw_pe) if not pd.isna(raw_pe) else None,
                    "median_pb": float(raw_pb) if not pd.isna(raw_pb) else None,
                    "median_roe": float(raw_roe) if not pd.isna(raw_roe) else None,
                    "peer_count": conservative_peer_count,
                    "peer_count_pe": valid_pe_count,
                    "peer_count_pb": valid_pb_count,
                    "peer_count_roe": valid_roe_count,
                    "median_peg": None,
                    "dispersion_iqr_median": None,
                    "source_type": "FALLBACK"
                })
                continue
                
            # Refinements
            # Note: peers is already a small subset (just the sector), so boolean indexing here is very fast.
            p1 = peers[
                (peers["market_cap_basic"].between(mcap * 0.2, mcap * 5.0)) &
                (peers["return_on_equity_fy"].between(roe - 10, roe + 10)) &
                (peers["total_revenue_yoy_growth_ttm"].between(growth - 15, growth + 15))
            ]
            
            p2 = peers[
                (peers["market_cap_basic"].between(mcap * 0.2, mcap * 5.0)) &
                (peers["return_on_equity_fy"].between(roe - 10, roe + 10))
            ]
            
            p3 = peers[
                (peers["market_cap_basic"].between(mcap * 0.2, mcap * 5.0))
            ]
            
            final_peers = peers
            refinement_level = "FULL"
            if len(p1) >= 8:
                final_peers = p1
                refinement_level = "P1"
            elif len(p2) >= 8:
                final_peers = p2
                refinement_level = "P2"
            elif len(p3) >= 8:
                final_peers = p3
                refinement_level = "P3"
                
            val_pe = final_peers["price_earnings_ttm"].median() if "price_earnings_ttm" in final_peers.columns else np.nan
            val_pb = final_peers["price_book_ratio"].median() if "price_book_ratio" in final_peers.columns else np.nan
            val_roe = final_peers["return_on_equity_fy"].median() if "return_on_equity_fy" in final_peers.columns else np.nan
            
            val_ev_ebitda = final_peers["enterprise_value_ebitda_ratio"].median() if "enterprise_value_ebitda_ratio" in final_peers.columns else None
            val_div_yield = final_peers["dividend_yield_recent"].median() if "dividend_yield_recent" in final_peers.columns else None
            
            median_peg = None
            if "total_revenue_yoy_growth_ttm" in final_peers.columns and "price_earnings_ttm" in final_peers.columns:
                peg_series = final_peers["price_earnings_ttm"] / final_peers["total_revenue_yoy_growth_ttm"].clip(lower=1)
                peg_series = peg_series.apply(lambda x: x if pd.notnull(x) and 0 < x < 10 else np.nan)
                median_peg = peg_series.dropna().median()
                
            dispersion = None
            if "price_earnings_ttm" in final_peers.columns and len(final_peers["price_earnings_ttm"].dropna()) >= 4:
                pe_series = final_peers["price_earnings_ttm"].dropna()
                q75, q25 = np.percentile(pe_series, [75, 25])
                iqr = q75 - q25
                med = pe_series.median()
                if med > 0:
                    dispersion = iqr / med
                    
        valid_pe_count = int(final_peers["price_earnings_ttm"].count()) if "price_earnings_ttm" in final_peers.columns else 0
        valid_pb_count = int(final_peers["price_book_ratio"].count()) if "price_book_ratio" in final_peers.columns else 0
        valid_roe_count = int(final_peers["return_on_equity_fy"].count()) if "return_on_equity_fy" in final_peers.columns else 0
        conservative_peer_count = min(valid_pe_count, valid_pb_count, valid_roe_count)
        
        source_type = "REFINED" if refinement_level in ("P1", "P2", "P3") else "FALLBACK"
        
        medians_map[symbol] = {
            "median_pe": float(val_pe) if not pd.isna(val_pe) else None,
            "median_pb": float(val_pb) if not pd.isna(val_pb) else None,
            "median_roe": float(val_roe) if not pd.isna(val_roe) else None,
            "median_ev_ebitda": float(val_ev_ebitda) if val_ev_ebitda is not None and not pd.isna(val_ev_ebitda) else None,
            "median_div_yield": float(val_div_yield) if val_div_yield is not None and not pd.isna(val_div_yield) else None,
            "peer_count": conservative_peer_count,
            "peer_count_pe": valid_pe_count,
            "peer_count_pb": valid_pb_count,
            "peer_count_roe": valid_roe_count,
            "median_peg": float(median_peg) if not pd.isna(median_peg) else 1.0,
            "dispersion_iqr_median": float(dispersion) if not pd.isna(dispersion) else None,
            "source_type": source_type
        }

    with _peer_medians_lock:
        _peer_medians_cache["ts"] = time.time()
        _peer_medians_cache["data"].update(medians_map)
        
    return medians_map
def norm_num(x):
    if x is None or pd.isna(x): return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

def norm_pct(x):
    if x is None or pd.isna(x): return None
    try:
        val = float(x)
        return val / 100.0 if abs(val) > 1 else val
    except (TypeError, ValueError):
        return None

def extract_raw_metrics(symbol, bse_code=None, ticker=None):
    try:
        import yfinance as yf
        from bse_mapping_utils import load_bse_mappings, save_bse_mapping
        
        clean_sym = symbol.strip().upper()
        mappings = load_bse_mappings()
        target_sym = f"{symbol}.NS"
        if clean_sym in mappings:
            target_sym = mappings[clean_sym]
        elif clean_sym.endswith(".NS") and clean_sym[:-3] in mappings:
            target_sym = mappings[clean_sym[:-3]]
        elif bse_code:
            target_sym = f"{bse_code}.BO"
            
        # [VERSION: VALUATION_RATE_LIMIT_FIX_v1.0] Wrap all yf calls under the global rate limiter
        # so this code path is visible to the circuit breaker and counts toward 429 detection.
        from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit
        
        if not ticker:
            # Check if primary ticker has data; if not, try BSE fallback
            yf_acquire(context=f"extract_raw_metrics | {target_sym}")
            try:
                ticker = yf.Ticker(target_sym)
                hist = ticker.history(period="1d")
            except Exception as hist_err:
                msg = str(hist_err).lower()
                if any(term in msg for term in ["too many requests", "rate limit", "429", "503", "502", "504", "connection termination", "upstream connect", "reset reason"]):
                    record_rate_limit(context=f"extract_raw_metrics | {target_sym}")
                raise hist_err
            finally:
                yf_release()
                
            if hist.empty:
                if target_sym.endswith(".NS"):
                    bse_sym = target_sym[:-3] + ".BO"
                    yf_acquire(context=f"extract_raw_metrics | {bse_sym}")
                    try:
                        ticker = yf.Ticker(bse_sym)
                        hist2 = ticker.history(period="1d")
                    except Exception:
                        hist2 = pd.DataFrame()
                    finally:
                        yf_release()
                    if not hist2.empty:
                        # [BUG-7 FIX] Strip suffix from symbol before saving so bare key is stored in DB
                        bare_sym = clean_sym[:-3] if clean_sym.endswith(".NS") or clean_sym.endswith(".BO") else clean_sym
                        save_bse_mapping(bare_sym, bse_sym)
                    elif bse_code:
                        yf_acquire(context=f"extract_raw_metrics | {bse_code}.BO")
                        try:
                            ticker = yf.Ticker(f"{bse_code}.BO")
                            hist3 = ticker.history(period="1d")
                        except Exception:
                            hist3 = pd.DataFrame()
                        finally:
                            yf_release()
                        if not hist3.empty:
                            bare_sym = clean_sym[:-3] if clean_sym.endswith(".NS") or clean_sym.endswith(".BO") else clean_sym
                            save_bse_mapping(bare_sym, f"{bse_code}.BO")
                elif bse_code:
                    yf_acquire(context=f"extract_raw_metrics | {bse_code}.BO")
                    try:
                        ticker = yf.Ticker(f"{bse_code}.BO")
                        hist4 = ticker.history(period="1d")
                    except Exception:
                        hist4 = pd.DataFrame()
                    finally:
                        yf_release()
                    if not hist4.empty:
                        bare_sym = clean_sym[:-3] if clean_sym.endswith(".NS") or clean_sym.endswith(".BO") else clean_sym
                        save_bse_mapping(bare_sym, f"{bse_code}.BO")
                
        _acquired = False
        try:
            yf_acquire(context=f"extract_raw_metrics.info | {target_sym}")
            _acquired = True
            try:
                info = ticker.info
            except Exception as info_err:
                msg = str(info_err).lower()
                if '401' in str(info_err) or 'Invalid Crumb' in str(info_err):
                    # Release the lock before the heavy crumb-cleanup work
                    yf_release()
                    _acquired = False
                    logger.warning(f"⚠️ YFinance crumb stale for {symbol}, clearing tzcache and retrying...")
                    import shutil, os
                    from config import BASE_DIR
                    tz_path = os.path.join(BASE_DIR, "data", "tzcache")
                    if os.path.exists(tz_path):
                        shutil.rmtree(tz_path, ignore_errors=True)
                    mappings = load_bse_mappings()
                    target_sym = f"{symbol}.NS"
                    if clean_sym in mappings:
                        target_sym = mappings[clean_sym]
                    elif clean_sym.endswith(".NS") and clean_sym[:-3] in mappings:
                        target_sym = mappings[clean_sym[:-3]]
                    elif bse_code:
                        target_sym = f"{bse_code}.BO"
                    yf_acquire(context=f"extract_raw_metrics.info.retry | {target_sym}")
                    _acquired = True
                    try:
                        ticker = yf.Ticker(target_sym)
                        info = ticker.info
                    finally:
                        yf_release()
                        _acquired = False
                else:
                    if 'too many requests' in msg or 'rate limit' in msg or '429' in msg:
                        record_rate_limit(context=f"extract_raw_metrics | {target_sym}")
                    raise info_err
        finally:
            if _acquired:
                yf_release()
        
        sector = info.get('sector')
        raw_industry = info.get('industry')
        from industry_normalizer import normalize_industry
        canonical_industry = normalize_industry(raw_industry)
        pe = norm_num(info.get('trailingPE') or info.get('peRatio'))
        pb = norm_num(info.get('priceToBook') or info.get('pbRatio'))
        roe = norm_pct(info.get('returnOnEquity'))
        eps = norm_num(info.get('trailingEps') or info.get('forwardEps'))
        bvps = norm_num(info.get('bookValue'))
        div_yield = norm_pct(info.get('dividendYield') or info.get('divYield'))
        current_price = norm_num(info.get('currentPrice') or info.get('regularMarketPrice'))
        
        if pe is None and eps is not None and eps > 0 and current_price is not None:
            pe = current_price / eps
        if pb is None and bvps is not None and bvps > 0 and current_price is not None:
            pb = current_price / bvps
            
        return {
            'sector': sector,
            'canonical_industry': canonical_industry,
            'pe': pe,
            'pb': pb,
            'roe': roe,
            'eps': eps,
            'bvps': bvps,
            'div_yield': div_yield
        }
    except Exception as e:
        logger.warning(f"Failed to extract raw metrics for {symbol}: {e}")
        return None


def compute_sector_medians(all_stocks):
    import statistics
    sector_data = {}
    for stock in all_stocks:
        sector = stock.get('sector')
        if not sector or sector == "Unknown":
            continue

        if sector not in sector_data:
            sector_data[sector] = {"pe_list": [], "pb_list": [], "roe_list": []}

        pe = norm_num(stock.get('pe'))
        if pe is not None and pe > 0:
            sector_data[sector]["pe_list"].append(pe)

        pb = norm_num(stock.get('pb'))
        if pb is not None and pb > 0:
            sector_data[sector]["pb_list"].append(pb)

        roe = norm_pct(stock.get('roe'))
        if roe is not None and roe > 0:
            sector_data[sector]["roe_list"].append(roe)

    medians = {}
    for sector, data in sector_data.items():
        pe_list = data["pe_list"]
        pb_list = data["pb_list"]
        roe_list = data["roe_list"]

        medians[sector] = {
            "median_pe": statistics.median(pe_list) if len(pe_list) >= 3 else None,
            "median_pb": statistics.median(pb_list) if len(pb_list) >= 3 else None,
            "median_roe": statistics.median(roe_list) if len(roe_list) >= 3 else None,
            "peer_count_pe": len(pe_list),
            "peer_count_pb": len(pb_list),
            "peer_count_roe": len(roe_list),
            "peer_count": min(len(pe_list), len(pb_list), len(roe_list)) if len(pe_list) > 0 else 0
        }
    return medians


def multi_scenario_dcf(fcf: float, growth_rate: float, discount_rate: float = 0.12, terminal_growth: float = 0.04, shares: float = 1.0):
    if not fcf or fcf <= 0 or not shares:
        return None
        
    def calculate_pv(g):
        pv = 0
        current_fcf = fcf
        # 5 year explicit forecast
        for i in range(1, 6):
            current_fcf *= (1 + g)
            pv += current_fcf / ((1 + discount_rate) ** i)
        # Terminal value
        tv = (current_fcf * (1 + terminal_growth)) / (discount_rate - terminal_growth)
        pv += tv / ((1 + discount_rate) ** 5)
        return pv / shares

    base_g = max(min(growth_rate, 0.20), 0.05)
    bear_g = base_g * 0.5
    bull_g = min(base_g * 1.5, 0.25)
    
    base_val = calculate_pv(base_g)
    bear_val = calculate_pv(bear_g)
    bull_val = calculate_pv(bull_g)
    
    weighted_value = (0.30 * bear_val) + (0.50 * base_val) + (0.20 * bull_val)
    
    return {
        "fair_value": weighted_value,
        "bear_value": bear_val,
        "bull_value": bull_val
    }
