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
                reverse_map = {}
                for sym in chunk:
                    clean_upper = sym.strip().upper()
                    is_bse = (
                        clean_upper in mappings or
                        (clean_upper.endswith(".NS") and clean_upper[:-3] in mappings) or
                        sym.isdigit() or sym.endswith(".BO") or sym.startswith("BSE:")
                    )
                    clean = sym.replace("BSE:", "").replace("NSE:", "").replace(".NS", "").replace(".BO", "")
                    f_sym = f"BSE:{clean}-EQ" if is_bse else f"NSE:{clean}-EQ"
                    fyers_symbols.append(f_sym)
                    reverse_map[f_sym] = sym
                
                try:
                    response = fyers.quotes({"symbols": ",".join(fyers_symbols)})
                    if response and isinstance(response, dict) and response.get("s") == "ok":
                        data_list = response.get("d", [])
                        for item in data_list:
                            if item.get("s") == "ok" and "v" in item and "lp" in item["v"]:
                                f_sym = item.get("n", "")
                                original_sym = reverse_map.get(f_sym)
                                lp = item["v"]["lp"]
                                if original_sym and lp and lp > 0:
                                    prices[original_sym] = float(lp)
                                    if original_sym in missing:
                                        missing.remove(original_sym)
                except Exception as e:
                    logger.warning(f"⚠️ Fyers quote fetch failed for batch {i//chunk_size}: {e}")
                    
    except ImportError:
        logger.warning("⚠️ fyers_auth not found, falling back strictly to yfinance.")
    except Exception as e:
        logger.warning(f"⚠️ Fyers client initialization failed: {e}")

    # ── 2. Attempt Fallback Fetch (Yahoo Finance fast_info) ───────────────────────────
    if missing:
        logger.info(f"🔄 Fyers fallback triggered. Fetching {len(missing)} symbols via yfinance batch download...")
        try:
            from bse_mapping_utils import load_bse_mappings
            mappings = load_bse_mappings()
        except Exception:
            mappings = {}
            
        yf_symbols = []
        yf_reverse_map = {}
        for sym in missing:
            clean_upper = sym.strip().upper()
            is_bse = (
                clean_upper in mappings or
                (clean_upper.endswith(".NS") and clean_upper[:-3] in mappings) or
                sym.isdigit() or sym.endswith(".BO") or sym.startswith("BSE:")
            )
            clean = sym.replace("BSE:", "").replace("NSE:", "").replace(".NS", "").replace(".BO", "")
            suffix = ".BO" if is_bse else ".NS"
            yf_sym = f"{clean}{suffix}"
            yf_symbols.append(yf_sym)
            yf_reverse_map[yf_sym] = sym
            
        try:
            # Batch fetch all at once, chunking by 100 to be safe
            import yfinance as yf
            chunk_size = 100
            for i in range(0, len(yf_symbols), chunk_size):
                chunk = yf_symbols[i:i+chunk_size]
                df = yf.download(" ".join(chunk), period="1d", group_by="ticker", progress=False, threads=True, auto_adjust=True)
                
                if len(chunk) == 1:
                    # Single ticker returns flat columns: Open, High, Low, Close
                    if not df.empty and "Close" in df.columns:
                        val = float(df["Close"].iloc[-1])
                        if val > 0:
                            prices[yf_reverse_map[chunk[0]]] = val
                else:
                    # Multiple tickers return MultiIndex columns
                    for y_sym in chunk:
                        try:
                            if y_sym in df.columns.levels[0]:
                                val = float(df[y_sym]["Close"].iloc[-1])
                                if val > 0:
                                    prices[yf_reverse_map[y_sym]] = val
                        except Exception as inner_e:
                            logger.warning(f"Failed to parse yf batch price for {y_sym}: {inner_e}")
                            
        except Exception as e:
            logger.exception(f"⚠️ YFinance batch fallback failed: {e}")
            
    return prices
