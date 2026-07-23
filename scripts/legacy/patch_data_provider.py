import sys

with open("app/data_provider.py", "r") as f:
    content = f.read()

# Import DataQualityValidator and MarketData
if "from data_quality import DataQualityValidator, MarketData" not in content:
    content = content.replace("from core_enums import ProviderResult", 
        "from core_enums import ProviderResult\nfrom data_quality import DataQualityValidator, MarketData")

# 1. Update YFinanceFetcher get_ohlcv
# It's tricky to regex, let's replace the whole method.
old_get_ohlcv_yf = """    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None) -> pd.DataFrame:
        # [VERSION: NULL_POINTER_FIX_v1.0]
        if not symbol:
            return None
        ns_sym = self._normalize_symbol(symbol)
        if not ns_sym:
            return None
        logger.debug(f"📥 Fetching OHLCV for {symbol} ({interval}, {period}) via YFinance...")
        df = self._get_ohlcv_raw(ns_sym, interval, period, retries, range_from, range_to)
        
        # If NSE query failed, retry once using the BSE (.BO) equivalent!
        if (isinstance(df, ProviderResult) and df in (ProviderResult.NOT_FOUND, ProviderResult.EMPTY_DATA)) or (df is None or (hasattr(df, 'empty') and df.empty)) and ns_sym.endswith(".NS"):
            bse_sym = ns_sym[:-3] + ".BO"
            logger.info(f"🔄 NSE fetch failed or returned empty for {symbol}. Retrying with BSE symbol {bse_sym}...")
            df = self._get_ohlcv_raw(bse_sym, interval, period, retries, range_from, range_to)
            if not isinstance(df, ProviderResult) and df is not None and not df.empty:
                try:
                    from bse_mapping_utils import save_bse_mapping
                    save_bse_mapping(symbol, bse_sym)
                except Exception as e:
                    logger.warning(f"Failed to save BSE mapping inside get_ohlcv: {e}")
            
        # [VERSION: POISONED_MAPPING_FIX_v1.0] Reverse Fallback for poisoned BSE mappings
        if (isinstance(df, ProviderResult) and df in (ProviderResult.NOT_FOUND, ProviderResult.EMPTY_DATA)) or (df is None or (hasattr(df, 'empty') and df.empty)) and ns_sym.endswith(".BO"):
            try:
                from bse_mapping_utils import load_bse_mappings, mark_bse_invalid
                mappings = load_bse_mappings()
                orig_clean = symbol.strip().upper()
                if orig_clean in mappings or (orig_clean.endswith(".NS") and orig_clean[:-3] in mappings):
                    logger.info(f"🗑️ Invalidating poisoned BSE mapping for {symbol} and retrying via NSE...")
                    clean_orig = orig_clean[:-3] if orig_clean.endswith(".NS") or orig_clean.endswith(".BO") else orig_clean
                    mark_bse_invalid(clean_orig)
                    recovery_sym = (orig_clean[:-3] + ".NS") if (orig_clean.endswith(".NS") or orig_clean.endswith(".BO")) else (orig_clean + ".NS")
                    df = self._get_ohlcv_raw(recovery_sym, interval, period, retries, range_from, range_to)
            except Exception as e:
                logger.warning(f"Failed during poisoned mapping recovery in get_ohlcv: {e}")

        return df"""

new_get_ohlcv_yf = """    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None) -> MarketData:
        if not symbol:
            return MarketData(None, "UNKNOWN", None, False, False, "No symbol")
        ns_sym = self._normalize_symbol(symbol)
        if not ns_sym:
            return MarketData(None, "UNKNOWN", None, False, False, "Normalization failed")
            
        logger.debug(f"📥 Fetching OHLCV for {symbol} ({interval}, {period}) via YFinance...")
        df = self._get_ohlcv_raw(ns_sym, interval, period, retries, range_from, range_to)
        
        used_fallback = False
        source = "NSE" if not ns_sym.endswith(".BO") else "BSE"
        report = None
        
        should_fallback = False
        if isinstance(df, ProviderResult) or df is None or getattr(df, 'empty', True):
            should_fallback = True
        else:
            report = DataQualityValidator.validate(df, period, interval, range_from, range_to)
            if not report.is_valid:
                logger.warning(f"NSE Data Quality Rejected for {symbol} (Score: {report.quality_score})")
                should_fallback = True

        if should_fallback and ns_sym.endswith(".NS"):
            bse_sym = ns_sym[:-3] + ".BO"
            logger.info(f"🔄 NSE fetch failed or poor quality for {symbol}. Retrying with BSE symbol {bse_sym}...")
            bse_df = self._get_ohlcv_raw(bse_sym, interval, period, retries, range_from, range_to)
            used_fallback = True
            
            if not isinstance(bse_df, ProviderResult) and bse_df is not None and not getattr(bse_df, 'empty', True):
                bse_report = DataQualityValidator.validate(bse_df, period, interval, range_from, range_to)
                if bse_report.is_valid:
                    df = bse_df
                    report = bse_report
                    source = "BSE"
                    try:
                        from bse_mapping_utils import save_bse_mapping
                        save_bse_mapping(symbol, bse_sym)
                    except Exception:
                        pass
                else:
                    logger.warning(f"BSE Fallback Quality Rejected for {symbol} (Score: {bse_report.quality_score})")
            
        if isinstance(df, ProviderResult):
            return MarketData(None, source, None, False, used_fallback, error=df.name)
            
        if report is None:
            report = DataQualityValidator.validate(df, period, interval, range_from, range_to)
            
        return MarketData(df if report.is_valid else None, source, report, False, used_fallback, None if report.is_valid else "Quality Check Failed")"""

content = content.replace(old_get_ohlcv_yf, new_get_ohlcv_yf)

with open("app/data_provider.py", "w") as f:
    f.write(content)
print("Replaced get_ohlcv in YFinanceFetcher")

