import logging
import pandas as pd
from typing import Optional, Dict
from .fyers_fetcher import FyersFetcher
from .provider_selector import selector
from data_registry import registry

import threading
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)

# Resource-specific lock for all external provider fetches
network_fetch_lock = threading.Lock()
_fyers_reauth_lock = threading.Lock()
_last_fyers_reauth_ts = 0.0

try:
    from .corporate_aliases import (
        CORPORATE_ACTION_ALIASES,
        get_alias_variants,
        strip_exchange_suffixes,
    )
except ImportError:
    from data_providers.corporate_aliases import (
        CORPORATE_ACTION_ALIASES,
        get_alias_variants,
        strip_exchange_suffixes,
    )

class UnifiedFetcher:
    """
    Data requested through this unified fetcher is logged in DatasetRegistry.
    Provider fallback policy is governed by ProviderSelector.
    """
    def __init__(self):
        self.registry = registry
        self.selector = selector
        self.fyers = FyersFetcher()

    def fetch_historical(self, symbol: str, interval: str, period: str, consumer: str) -> Optional[pd.DataFrame]:
        from config import NON_EQUITY_BLOCKLIST
        orig_symbol = symbol
        clean_sym = str(symbol).replace('.NS', '').replace('.BO', '').replace('BSE:', '').replace('NSE:', '').strip().upper()
        if clean_sym in CORPORATE_ACTION_ALIASES:
            aliased = CORPORATE_ACTION_ALIASES[clean_sym]
            suffix = ".NS" if str(symbol).endswith(".NS") else (".BO" if str(symbol).endswith(".BO") else "")
            symbol = f"{aliased}{suffix}"
            logger.info(f"ℹ️ [UnifiedFetcher] Corporate action alias mapped '{orig_symbol}' -> '{symbol}'")

        if clean_sym in NON_EQUITY_BLOCKLIST:
            logger.info(f"ℹ️ Skipping non-equity trust {symbol} in UnifiedFetcher")
            return pd.DataFrame()

        logger.info(f"[{consumer}] Fetching {symbol} ({interval} / {period}) via UnifiedFetcher")
        
        dataset_id = f"price_{interval}"
        if self.registry.get_entry(dataset_id):
            self.registry.register_consumer(dataset_id, consumer)
            
        providers = self.selector.get_providers(dataset_id, fetch_type="historical")
        
        provider_errors = {}
        for provider in providers:
            if provider == "upstox":
                try:
                    from market_data.providers.upstox_provider import UpstoxProvider
                    upstox_fetcher = UpstoxProvider(auth_service=None)
                    md = upstox_fetcher.get_ohlcv(symbol, interval=interval, period=period)
                    if md is not None and getattr(md, 'dataframe', None) is not None and not md.dataframe.empty:
                        logger.info(f"✅ [Upstox] Successfully fetched historical {symbol}")
                        entry = self.registry.get_entry(dataset_id)
                        if entry:
                            entry.provider_used = "upstox"
                            is_fallback = entry.preferred_provider and provider != entry.preferred_provider
                            md.dataframe.attrs = {
                                "dataset": dataset_id,
                                "provider": provider,
                                "preferred_provider": entry.preferred_provider,
                                "fallback_used": bool(is_fallback),
                                "fetch_timestamp": datetime.now(IST).isoformat()
                            }
                        from trading_calendar import enforce_trading_day_candles
                        return enforce_trading_day_candles(md.dataframe, symbol)
                    else:
                        err = getattr(md, "error", "No error message provided") if md else "No MarketData returned"
                        provider_errors["upstox"] = err
                        logger.debug(f"⚠️ [Upstox] Skipped historical {symbol}: {err}")
                except Exception as e:
                    provider_errors["upstox"] = str(e)
                    logger.warning(f"⚠️ [Upstox] Failed to fetch historical {symbol}: {e}")

            elif provider == "fyers":
                try:
                    md = self.fyers.get_ohlcv(symbol, interval=interval, period=period)
                    df = getattr(md, "dataframe", getattr(md, "df", None))
                    if df is not None and not df.empty:
                        logger.info(f"✅ [Fyers] Successfully fetched historical {symbol}")
                        entry = self.registry.get_entry(dataset_id)
                        if entry:
                            entry.provider_used = "fyers"
                            is_fallback = entry.preferred_provider and provider != entry.preferred_provider
                            df.attrs = {
                                "dataset": dataset_id,
                                "provider": provider,
                                "preferred_provider": entry.preferred_provider,
                                "fallback_used": bool(is_fallback),
                                "fetch_timestamp": datetime.now(IST).isoformat()
                            }
                        from trading_calendar import enforce_trading_day_candles
                        return enforce_trading_day_candles(df, symbol)
                    else:
                        err = getattr(md, "error", "No error message provided") if md else "No MarketData returned"
                        provider_errors["fyers"] = err
                        logger.debug(f"⚠️ [Fyers] Skipped historical {symbol}: {err}")
                except Exception as e:
                    provider_errors["fyers"] = str(e)
                    logger.warning(f"⚠️ [Fyers] Failed to fetch historical {symbol}: {e}")

        error_details = ", ".join([f"{p}: {e}" for p, e in provider_errors.items()])
        from config import PROVIDER_UNAVAILABLE_SYMBOLS
        is_expected_miss = (
            any("auth" in str(e).lower() or "uninitialized" in str(e).lower() for e in provider_errors.values())
            or clean_sym in CORPORATE_ACTION_ALIASES
            or orig_symbol in CORPORATE_ACTION_ALIASES
            or any(s in str(symbol).upper() for s in ("NIFTY", "BANKNIFTY", "SENSEX", "INDIAVIX"))
            or clean_sym in PROVIDER_UNAVAILABLE_SYMBOLS
            or orig_symbol in PROVIDER_UNAVAILABLE_SYMBOLS
        )
        if is_expected_miss:
            logger.warning(f"⚠️ Exhausted providers for historical {orig_symbol} (fallback/unauthenticated/corporate action): {error_details}")
        else:
            logger.error(f"❌ Exhausted all providers for historical {orig_symbol}. Reasons: {error_details}")
        return pd.DataFrame()


    @staticmethod
    def _store_live_quote_result(
        results: dict,
        pending: set,
        results_lock: threading.Lock,
        orig: str,
        cmd_data: dict,
    ) -> None:
        """Stores fetched live quote payload for orig and all its canonical/corporate aliases."""
        clean_orig = strip_exchange_suffixes(orig)
        with results_lock:
            results[orig] = {"v": {"cmd": cmd_data}}
            if clean_orig:
                results[clean_orig] = {"v": {"cmd": cmd_data}}
                results[clean_orig + ".NS"] = {"v": {"cmd": cmd_data}}
                for variant in get_alias_variants(clean_orig, include_reverse=True):
                    results[variant] = {"v": {"cmd": cmd_data}}
                    results[variant + ".NS"] = {"v": {"cmd": cmd_data}}
                    pending.discard(variant)
                    pending.discard(variant + ".NS")
            pending.discard(orig)
            pending.discard(clean_orig)
            pending.discard(clean_orig + ".NS")

    def _consume_fyers_quotes_response(
        self,
        resp: dict,
        fyers_map: dict,
        results: dict,
        pending: set,
        results_lock: threading.Lock,
        extract_cmd_fn,
    ) -> tuple[int, list[str]]:
        """Parses a Fyers quotes payload into results/pending and returns (success_count, missed_orig_symbols)."""
        success_count = 0
        succeeded_origs = set()
        for item in resp.get("d", []):
            if item.get("s") == "ok" and "v" in item and "lp" in item["v"]:
                sym_name = item.get("n")
                orig = fyers_map.get(sym_name)
                if orig:
                    cmd_data = extract_cmd_fn(item["v"])
                    self._store_live_quote_result(results, pending, results_lock, orig, cmd_data)
                    logger.debug(f"✅ [Fyers] Successfully fetched live quote for {orig} ({sym_name}): ₹{cmd_data['c']:.2f}")
                    succeeded_origs.add(orig)
                    success_count += 1
        missed = [orig for orig in fyers_map.values() if orig not in succeeded_origs]
        return success_count, missed

    def fetch_live_quotes(self, symbols: list[str], consumer: str) -> dict[str, dict]:
        """
        Fetches live snapshot data (quotes) for a list of symbols.
        Uses ProviderSelector for routing.
        Returns a dict of symbol -> quote data mapping.
        """
        valid_symbols = []
        for s in symbols:
            if not s or not isinstance(s, str):
                continue
            clean = str(s).replace('.NS', '').replace('.BO', '').replace('BSE:', '').replace('NSE:', '').strip().upper()
            if clean and clean not in ('?', 'NONE', 'NAN', 'NULL', 'UNKNOWN') and any(c.isalnum() for c in clean):
                valid_symbols.append(s)

        if not valid_symbols:
            return {}
        symbols = valid_symbols

        with network_fetch_lock:
            logger.info(f"[{consumer}] Fetching live quotes for {len(symbols)} symbols via UnifiedFetcher")
            dataset_id = "live_quotes"
            if self.registry.get_entry(dataset_id):
                self.registry.register_consumer(dataset_id, consumer)
                
            providers = self.selector.get_providers(dataset_id, fetch_type="live_quotes")
            # Ensure Upstox is primary for exchange indices per AGENTS.md real data rules
            if "upstox" in providers and (consumer == "dashboard_indices" or any(s in ("NIFTY 50", "BANKNIFTY", "SENSEX") for s in symbols)):
                providers = ["upstox"] + [p for p in providers if p != "upstox"]
            results: Dict[str, dict] = {}
            pending = set(symbols)
            results_lock = threading.Lock()
            
            for provider in providers:
                if not pending:
                    break
                    
                if provider == "fyers":
                    logger.info(f"🔄 [Fyers] Fetching live quotes for {len(pending)} symbols...")
                    import concurrent.futures

                    def _extract_cmd_dict(v_dict: dict) -> dict:
                        val = v_dict.get("lp") if "lp" in v_dict else v_dict.get("c")
                        ch = v_dict.get("ch")
                        chp = v_dict.get("chp")
                        val_flt = float(val) if val is not None else None
                        ch_flt = float(ch) if ch is not None else None
                        chp_flt = float(chp) if chp is not None else None
                        if val_flt is not None and ch_flt is not None:
                            pc_flt = round(val_flt - ch_flt, 2)
                            if chp_flt is None and pc_flt > 0:
                                chp_flt = round((ch_flt / pc_flt) * 100, 2)
                        else:
                            pc = v_dict.get("prev_close_price") or v_dict.get("pc")
                            pc_flt = float(pc) if pc is not None else None
                            if ch_flt is None and val_flt is not None and pc_flt is not None:
                                ch_flt = round(val_flt - pc_flt, 2)
                            if chp_flt is None and val_flt is not None and pc_flt and pc_flt > 0:
                                chp_flt = round(((val_flt - pc_flt) / pc_flt) * 100, 2)
                        return {"c": val_flt, "pc": pc_flt, "ch": ch_flt, "chp": chp_flt}

                    def _retry_fyers_missed_symbols(fyers_client, missed_origs, tried_map):
                        """Refreshes stale Fyers mapper once if needed and retries unresolved symbols via ISIN/alias."""
                        if not missed_origs:
                            return 0
                        try:
                            from data_providers.fyers_symbol_mapper import fyers_mapper
                        except ImportError:
                            from .fyers_symbol_mapper import fyers_mapper
                        fyers_mapper.refresh_if_stale()
                        retry_map = {}
                        for m_orig in missed_origs:
                            cand_norm = fyers_mapper.get_fyers_symbol(m_orig)
                            if not cand_norm or cand_norm in tried_map:
                                clean_m = strip_exchange_suffixes(m_orig)
                                for v_sym in get_alias_variants(clean_m, include_reverse=False):
                                    c_norm = fyers_mapper.get_fyers_symbol(v_sym)
                                    if c_norm and c_norm not in tried_map:
                                        cand_norm = c_norm
                                        break
                            if cand_norm and cand_norm not in tried_map:
                                retry_map[cand_norm] = m_orig
                        if not retry_map:
                            return 0
                        r_resp = fyers_client.quotes({"symbols": ",".join(retry_map.keys())})
                        if r_resp and isinstance(r_resp, dict) and r_resp.get("s") == "ok":
                            r_ok, _ = self._consume_fyers_quotes_response(
                                r_resp, retry_map, results, pending, results_lock, _extract_cmd_dict
                            )
                            if r_ok > 0:
                                logger.info(f"✅ [Fyers] Recovered {r_ok}/{len(retry_map)} quotes via ISIN/alias retry.")
                                try:
                                    from data_providers.fyers_mapping_utils import save_fyers_mapping
                                    for item in r_resp.get("d", []):
                                        if item.get("s") == "ok" and item.get("n") in retry_map:
                                            orig_r = retry_map[item["n"]]
                                            save_fyers_mapping(strip_exchange_suffixes(orig_r), item["n"])
                                except Exception:
                                    pass
                            return r_ok
                        return 0

                    def fetch_fyers_chunk(chunk):
                        fyers_map = {}
                        for orig in chunk:
                            norm = self.fyers._normalize_symbol(orig)
                            if norm and (norm.startswith("NSE:") or norm.startswith("BSE:") or norm.startswith("MCX:")):
                                fyers_map[norm] = orig

                        if not fyers_map:
                            return

                        try:
                            from fyers_auth import get_fyers_client
                            fyers_client = get_fyers_client()
                            if fyers_client:
                                fyers_symbols_str = ",".join(fyers_map.keys())
                                resp = fyers_client.quotes({"symbols": fyers_symbols_str})

                                if resp and isinstance(resp, dict) and resp.get("s") == "ok":
                                    success_count, missed = self._consume_fyers_quotes_response(
                                        resp, fyers_map, results, pending, results_lock, _extract_cmd_dict
                                    )
                                    if missed:
                                        success_count += _retry_fyers_missed_symbols(fyers_client, missed, fyers_map)
                                    if success_count > 0:
                                        logger.info(f"✅ [Fyers] Fetched {success_count}/{len(fyers_map)} quotes successfully.")
                                else:
                                    logger.warning(f"⚠️ [Fyers] Quote batch response not ok for {len(fyers_map)} symbols: {resp}")
                                    code = resp.get("code") if isinstance(resp, dict) else None
                                    msg = str(resp.get("message", "")).lower() if isinstance(resp, dict) else ""
                                    if str(code) in ["-15", "-16", "401", "-401", "494"] or "valid token" in msg or "authenticate" in msg:
                                        global _last_fyers_reauth_ts
                                        new_client = None
                                        with _fyers_reauth_lock:
                                            import time as _t
                                            _now = _t.time()
                                            if _now - _last_fyers_reauth_ts > 30.0:
                                                logger.info("🔄 Fyers daily token expired during live quotes batch. Initiating automated re-authentication...")
                                                from fyers_auth import auto_login, get_fyers_client
                                                if auto_login():
                                                    _last_fyers_reauth_ts = _t.time()
                                                    new_client = get_fyers_client()
                                                else:
                                                    logger.error("❌ Fyers automated re-authentication failed during live quotes batch.")
                                            else:
                                                from fyers_auth import get_fyers_client
                                                new_client = get_fyers_client()

                                        if new_client:
                                            resp2 = new_client.quotes({"symbols": fyers_symbols_str})
                                            if resp2 and isinstance(resp2, dict) and resp2.get("s") == "ok":
                                                success_count, missed = self._consume_fyers_quotes_response(
                                                    resp2, fyers_map, results, pending, results_lock, _extract_cmd_dict
                                                )
                                                if missed:
                                                    success_count += _retry_fyers_missed_symbols(new_client, missed, fyers_map)
                                                if success_count > 0:
                                                    logger.info(f"✅ [Fyers] Fetched {success_count}/{len(fyers_map)} quotes successfully on RETRY.")
                        except Exception as e:
                            logger.warning(f"⚠️ [Fyers] Batch quote fetch failed: {e}")

                    pending_list = list(pending)
                    chunk_size = 50
                    chunks = [pending_list[i:i+chunk_size] for i in range(0, len(pending_list), chunk_size)]
                    
                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(chunks) or 1)) as executor:
                        executor.map(fetch_fyers_chunk, chunks)
                        
                elif provider == "upstox":
                    logger.info(f"🔄 [Upstox] Fetching live quotes for {len(pending)} symbols...")
                    try:
                        from market_data.providers.upstox_provider import UpstoxProvider
                        upstox_fetcher = UpstoxProvider(auth_service=None)
                        pending_list = list(pending)
                        
                        chunk_size = 500
                        chunks = [pending_list[i:i+chunk_size] for i in range(0, len(pending_list), chunk_size)]
                        
                        for chunk in chunks:
                            resp = upstox_fetcher.fetch_live_quotes_batch(chunk)
                            if resp:
                                success_count = 0
                                for orig in chunk:
                                    try:
                                        raw_key = upstox_fetcher._get_instrument_key(orig) or ""
                                        clean_sym = raw_key.split(":")[-1].split("|")[-1] if raw_key else ""
                                        clean_orig = strip_exchange_suffixes(orig)
                                        quote_data = (
                                            resp.get(orig)
                                            or resp.get(clean_orig)
                                            or resp.get(clean_orig + ".NS")
                                            or (resp.get(clean_sym) if clean_sym else None)
                                            or (resp.get(raw_key) if raw_key else None)
                                            or (resp.get(raw_key.replace("|", ":")) if raw_key else None)
                                        )
                                        if not quote_data and clean_orig:
                                            for cand in get_alias_variants(clean_orig, include_reverse=True):
                                                quote_data = (
                                                    resp.get(cand)
                                                    or resp.get(cand + ".NS")
                                                    or resp.get(f"NSE_EQ:{cand}")
                                                    or resp.get(f"NSE_EQ|{cand}")
                                                    or resp.get(f"BSE_EQ:{cand}")
                                                    or resp.get(f"BSE_EQ|{cand}")
                                                )
                                                if quote_data:
                                                    break
                                        
                                        if quote_data and isinstance(quote_data, dict):
                                            val = quote_data.get("last_price") or quote_data.get("lp") or quote_data.get("cp")
                                            if (val is None or float(val or 0) <= 0) and "ohlc" in quote_data and isinstance(quote_data["ohlc"], dict):
                                                val = quote_data["ohlc"].get("close") or quote_data["ohlc"].get("open")
                                            if val is not None and float(val) > 0:
                                                val_flt = float(val)
                                                ch_val = quote_data.get("net_change") or quote_data.get("ch")
                                                try:
                                                    ch_flt = float(ch_val) if ch_val is not None else None
                                                except Exception:
                                                    ch_flt = None

                                                chp_val = quote_data.get("chp")
                                                try:
                                                    chp_flt = float(chp_val) if chp_val is not None else None
                                                except Exception:
                                                    chp_flt = None

                                                if ch_flt is not None and val_flt is not None:
                                                    pc_flt = round(val_flt - ch_flt, 2)
                                                    if chp_flt is None and pc_flt > 0:
                                                        chp_flt = round((ch_flt / pc_flt) * 100, 2)
                                                else:
                                                    pc_val = None
                                                    if "ohlc" in quote_data and isinstance(quote_data["ohlc"], dict):
                                                        pc_val = quote_data["ohlc"].get("close")
                                                    if pc_val is None:
                                                        pc_val = quote_data.get("prev_close_price") or quote_data.get("pc") or quote_data.get("cp")
                                                    try:
                                                        pc_flt = float(pc_val) if pc_val is not None else None
                                                    except Exception:
                                                        pc_flt = None
                                                    if ch_flt is None and val_flt is not None and pc_flt is not None:
                                                        ch_flt = round(val_flt - pc_flt, 2)
                                                    if chp_flt is None and val_flt is not None and pc_flt and pc_flt > 0:
                                                        chp_flt = round(((val_flt - pc_flt) / pc_flt) * 100, 2)

                                                cmd_data = {"c": val_flt, "pc": pc_flt, "ch": ch_flt, "chp": chp_flt}
                                                self._store_live_quote_result(results, pending, results_lock, orig, cmd_data)
                                                logger.debug(f"✅ [Upstox] Successfully fetched live quote for {orig}: ₹{val_flt:.2f}")
                                                success_count += 1
                                    except Exception as item_err:
                                        logger.error(f"❌ [Upstox] Quote parsing error for symbol {orig}: {item_err}", exc_info=True)
                                if success_count > 0:
                                    logger.info(f"✅ [Upstox] Fetched {success_count}/{len(chunk)} quotes successfully.")
                    except Exception as e:
                        logger.error(f"❌ [Upstox] Batch quote fetch failed: {e}", exc_info=True)



        # MANDATORY ZERO-SYNTHETIC-FALLBACK: Never substitute stale DB prices when live quotes fail.
        # Fail closed: un-fetched symbols remain None so scanners block safely instead of using stale quotes.

        if pending:
            logger.warning(f"⚠️ Live quotes unavailable from providers for ({len(pending)}): {sorted(list(pending))}")
            for p_sym in list(pending):
                clean_p = p_sym.replace(".NS", "").replace(".BO", "")
                results[p_sym] = {"v": {"cmd": {"c": None}}}
                results[clean_p] = {"v": {"cmd": {"c": None}}}
            
        if self.registry.get_entry(dataset_id):
            self.registry.get_entry(dataset_id).provider_used = "live_batch"
            
        return results

# Global instance
fetcher = UnifiedFetcher()
