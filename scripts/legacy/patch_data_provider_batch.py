import sys

with open("app/data_provider.py", "r") as f:
    content = f.read()

old_batch = """    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, pd.DataFrame]:
        prefix = f"[{caller}] " if caller else ""
        logger.info(f"{prefix}📥 Fetching batch OHLCV for {len(symbols)} symbols ({interval}, {period}) via YFinance...")
        
        normalized_map = {}
        for s in symbols:
            # [VERSION: NULL_POINTER_FIX_v1.0]
            if not s:
                continue
            ns_sym = self._normalize_symbol(s)
            if not ns_sym:
                continue
            if ns_sym not in normalized_map:
                normalized_map[ns_sym] = []
            normalized_map[ns_sym].append(s)
            
        ns_symbols = list(normalized_map.keys())
        results = self._fetch_batch_raw(ns_symbols, period, interval, range_from, range_to)
        
        # [VERSION: YF_DYNAMIC_BSE_FALLBACK_v1.0] Single bulk BSE fallback
        missing_symbols = []
        for ns_sym in ns_symbols:
            df = results.get(ns_sym)
            if isinstance(df, ProviderResult) or df is None or getattr(df, 'empty', True):
                missing_symbols.append(ns_sym)
                
        if missing_symbols:
            logger.info(f"🔄 {len(missing_symbols)} NSE symbols failed batch fetch. Attempting bulk BSE fallback...")
            bse_fetch_list = []
            bse_to_ns_map = {}
            for ns_sym in missing_symbols:
                if ns_sym.endswith(".NS"):
                    bse_sym = ns_sym[:-3] + ".BO"
                    bse_fetch_list.append(bse_sym)
                    bse_to_ns_map[bse_sym] = ns_sym
                    
            if bse_fetch_list:
                bse_results = self._fetch_batch_raw(bse_fetch_list, period, interval, range_from, range_to)
                for bse_sym, df in bse_results.items():
                    ns_sym = bse_to_ns_map[bse_sym]
                    if not isinstance(df, ProviderResult) and df is not None and not getattr(df, 'empty', True):
                        results[ns_sym] = df
                        # Save mapping
                        orig_symbols = normalized_map.get(ns_sym, [])
                        try:
                            from bse_mapping_utils import save_bse_mapping
                            for orig in orig_symbols:
                                save_bse_mapping(orig, bse_sym)
                        except Exception:
                            pass
                            
        # Reverse map to original requested symbols
        final_results = {}
        for ns_sym, df in results.items():
            for orig_sym in normalized_map.get(ns_sym, []):
                final_results[orig_sym] = df
                
        for s in symbols:
            final_results.setdefault(s, None)
            
        return final_results"""

new_batch = """    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, MarketData]:
        prefix = f"[{caller}] " if caller else ""
        logger.info(f"{prefix}📥 Fetching batch OHLCV for {len(symbols)} symbols ({interval}, {period}) via YFinance...")
        
        normalized_map = {}
        for s in symbols:
            if not s: continue
            ns_sym = self._normalize_symbol(s)
            if not ns_sym: continue
            if ns_sym not in normalized_map:
                normalized_map[ns_sym] = []
            normalized_map[ns_sym].append(s)
            
        ns_symbols = list(normalized_map.keys())
        results = self._fetch_batch_raw(ns_symbols, period, interval, range_from, range_to)
        
        reports = {}
        missing_symbols = []
        for ns_sym in ns_symbols:
            df = results.get(ns_sym)
            if isinstance(df, ProviderResult) or df is None or getattr(df, 'empty', True):
                missing_symbols.append(ns_sym)
            else:
                report = DataQualityValidator.validate(df, period, interval, range_from, range_to)
                if not report.is_valid:
                    missing_symbols.append(ns_sym)
                else:
                    reports[ns_sym] = report
                
        if missing_symbols:
            bse_fetch_list = []
            bse_to_ns_map = {}
            for ns_sym in missing_symbols:
                if ns_sym.endswith(".NS"):
                    bse_sym = ns_sym[:-3] + ".BO"
                    bse_fetch_list.append(bse_sym)
                    bse_to_ns_map[bse_sym] = ns_sym
                    
            if bse_fetch_list:
                logger.info(f"🔄 {len(bse_fetch_list)} NSE symbols failed/poor quality. Attempting bulk BSE fallback...")
                bse_results = self._fetch_batch_raw(bse_fetch_list, period, interval, range_from, range_to)
                for bse_sym, df in bse_results.items():
                    ns_sym = bse_to_ns_map[bse_sym]
                    if not isinstance(df, ProviderResult) and df is not None and not getattr(df, 'empty', True):
                        bse_report = DataQualityValidator.validate(df, period, interval, range_from, range_to)
                        if bse_report.is_valid:
                            results[ns_sym] = df
                            reports[ns_sym] = bse_report
                            try:
                                from bse_mapping_utils import save_bse_mapping
                                for orig in normalized_map.get(ns_sym, []):
                                    save_bse_mapping(orig, bse_sym)
                            except Exception:
                                pass
                            
        final_results = {}
        for ns_sym, df in results.items():
            used_fallback = ns_sym in missing_symbols
            source = "BSE" if used_fallback else "NSE"
            report = reports.get(ns_sym)
            for orig_sym in normalized_map.get(ns_sym, []):
                if isinstance(df, ProviderResult):
                    final_results[orig_sym] = MarketData(None, source, None, False, used_fallback, error=df.name)
                elif report is not None and report.is_valid:
                    final_results[orig_sym] = MarketData(df, source, report, False, used_fallback, None)
                else:
                    final_results[orig_sym] = MarketData(None, source, report, False, used_fallback, "Quality Rejected")
                
        for s in symbols:
            if s not in final_results:
                final_results[s] = MarketData(None, "UNKNOWN", None, False, False, "Missing")
            
        return final_results"""

content = content.replace(old_batch, new_batch)
with open("app/data_provider.py", "w") as f:
    f.write(content)
print("Replaced get_batch_ohlcv in YFinanceFetcher")

