import logging
from typing import Dict, List
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

def get_live_prices(symbols: List[str]) -> Dict[str, float]:
    """
    Fetches real-time Last Traded Price (CMP) for a list of standard NSE symbols (e.g., ['CAMS', 'KRBL']).
    Primary Source: Fyers Broker API (batch quotes up to 50 symbols).
    Fallback Source: yfinance fast_info engine (concurrent execution).
    """
    if not symbols:
        return {}

    prices = {}
    missing = list(symbols)

    # ── 1. Attempt Primary Fetch (Fyers) ────────────────────────────────────────────────
    try:
        from fyers_auth import get_fyers_client
        fyers = get_fyers_client()
        if fyers is not None:
            # Chunk symbols into blocks of 50 (Fyers API Limit)
            chunk_size = 50
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                
                # [VERSION: LIVE_PRICES_BSE_FIX_v1.1] Route BSE-only, numeric, or cached BSE fallback tickers to BSE
                try:
                    from bse_mapping_utils import load_bse_mappings
                    mappings = load_bse_mappings()
                except Exception:
                    mappings = {}

                fyers_symbols = []
                for sym in chunk:
                    clean_upper = sym.strip().upper()
                    is_bse = (
                        clean_upper in mappings or
                        (clean_upper.endswith(".NS") and clean_upper[:-3] in mappings) or
                        sym.isdigit() or sym.endswith(".BO") or sym.startswith("BSE:")
                    )
                    clean = sym.replace("BSE:", "").replace("NSE:", "").replace(".NS", "").replace(".BO", "")
                    if is_bse:
                        fyers_symbols.append(f"BSE:{clean}-EQ")
                    else:
                        fyers_symbols.append(f"NSE:{clean}-EQ")
                
                try:
                    response = fyers.quotes({"symbols": ",".join(fyers_symbols)})
                    if response and isinstance(response, dict) and response.get("s") == "ok":
                        data_list = response.get("d", [])
                        for item in data_list:
                            # Fyers returns {'n': 'NSE:CAMS-EQ', 's': 'ok', 'v': {'lp': 790.5, ...}}
                            if item.get("s") == "ok" and "v" in item and "lp" in item["v"]:
                                raw_sym = item.get("n", "").replace("NSE:", "").replace("-EQ", "")
                                lp = item["v"]["lp"]
                                if lp and lp > 0:
                                    prices[raw_sym] = float(lp)
                                    if raw_sym in missing:
                                        missing.remove(raw_sym)
                except Exception as e:
                    logger.warning(f"⚠️ Fyers quote fetch failed for batch {i//chunk_size}: {e}")
                    
    except ImportError:
        logger.warning("⚠️ fyers_auth not found, falling back strictly to yfinance.")
    except Exception as e:
        logger.warning(f"⚠️ Fyers client initialization failed: {e}")

    # ── 2. Attempt Fallback Fetch (Yahoo Finance fast_info) ───────────────────────────
    if missing:
        logger.info(f"🔄 Fyers fallback triggered. Fetching {len(missing)} symbols via yfinance...")
        
        def _get_yf_price(sym: str):
            try:
                # [VERSION: LIVE_PRICES_BSE_FIX_v1.1] Route BSE-only, numeric, or cached BSE fallback tickers to BSE
                try:
                    from bse_mapping_utils import load_bse_mappings
                    mappings = load_bse_mappings()
                except Exception:
                    mappings = {}
                    
                clean_upper = sym.strip().upper()
                is_bse = (
                    clean_upper in mappings or
                    (clean_upper.endswith(".NS") and clean_upper[:-3] in mappings) or
                    sym.isdigit() or sym.endswith(".BO") or sym.startswith("BSE:")
                )
                clean = sym.replace("BSE:", "").replace("NSE:", "").replace(".NS", "").replace(".BO", "")
                suffix = ".BO" if is_bse else ".NS"
                val = float(yf.Ticker(f"{clean}{suffix}").fast_info.last_price)
                return sym, val
            except Exception:
                return sym, None

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(_get_yf_price, sym): sym for sym in missing}
            for future in as_completed(futures):
                sym, val = future.result()
                if val and val > 0:
                    prices[sym] = val
                    
    return prices
