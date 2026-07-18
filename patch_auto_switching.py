import sys

with open("app/data_provider.py", "r") as f:
    content = f.read()

# AutoSwitchingFetcher gets MarketData, we need to adapt it.
old_get_ohlcv = """    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None) -> pd.DataFrame:
        if self._should_use_fyers():
            try:
                df = self.fyers_fetcher.get_ohlcv(symbol, interval, period, retries, range_from, range_to)
                if df is not None and not df.empty:
                    return df
                logger.warning(f"Fyers fetch returned empty/failed for {symbol}. Falling back to YFinance.")
            except Exception as e:
                logger.warning(f"Fyers fetch exception for {symbol}: {e}. Falling back to YFinance.")
        return self.yfinance_fetcher.get_ohlcv(symbol, interval, period, retries, range_from, range_to)"""

new_get_ohlcv = """    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None) -> MarketData:
        if self._should_use_fyers():
            try:
                md = self.fyers_fetcher.get_ohlcv(symbol, interval, period, retries, range_from, range_to)
                if md.dataframe is not None and md.quality_report is not None and md.quality_report.is_valid:
                    return md
                logger.warning(f"Fyers fetch returned poor quality for {symbol} (Score: {md.quality_report.quality_score if md.quality_report else 0}). Falling back to YFinance.")
            except Exception as e:
                logger.warning(f"Fyers fetch exception for {symbol}: {e}. Falling back to YFinance.")
        return self.yfinance_fetcher.get_ohlcv(symbol, interval, period, retries, range_from, range_to)"""

old_get_batch_ohlcv = """    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, pd.DataFrame]:
        if self._should_use_fyers():
            try:
                results = self.fyers_fetcher.get_batch_ohlcv(symbols, interval, period, retries, range_from, range_to, caller=caller)
                missing_symbols = [s for s in symbols if results.get(s) is None or results[s].empty]
                if missing_symbols:
                    if len(missing_symbols) == len(symbols):
                        logger.warning(f"Fyers Batch API Silent Failure: Fyers returned empty data for ALL {len(symbols)} symbols. Falling back to Yahoo Finance.")
                    logger.warning(f"Fyers batch fetch returned empty/missing data for {len(missing_symbols)} symbols. Querying YFinance for these.")
                    yf_results = self.yfinance_fetcher.get_batch_ohlcv(missing_symbols, interval, period, retries, range_from, range_to, caller=caller)
                    for s in missing_symbols:
                        if s in yf_results:
                            results[s] = yf_results[s]
                for s in symbols:
                    results.setdefault(s, None)
                return results
            except Exception as e:
                logger.warning(f"Fyers batch fetch exception: {e}. Falling back to YFinance.")
        
        yf_fallback_results = self.yfinance_fetcher.get_batch_ohlcv(symbols, interval, period, retries, range_from, range_to, caller=caller)
        for s in symbols:
            yf_fallback_results.setdefault(s, None)
        return yf_fallback_results"""

new_get_batch_ohlcv = """    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, MarketData]:
        if self._should_use_fyers():
            try:
                results = self.fyers_fetcher.get_batch_ohlcv(symbols, interval, period, retries, range_from, range_to, caller=caller)
                missing_symbols = [s for s in symbols if not results.get(s) or results[s].dataframe is None or not results[s].quality_report or not results[s].quality_report.is_valid]
                if missing_symbols:
                    if len(missing_symbols) == len(symbols):
                        logger.warning(f"Fyers Batch API Silent Failure: Fyers returned poor data for ALL {len(symbols)} symbols. Falling back to Yahoo Finance.")
                    logger.warning(f"Fyers batch fetch returned poor quality data for {len(missing_symbols)} symbols. Querying YFinance for these.")
                    yf_results = self.yfinance_fetcher.get_batch_ohlcv(missing_symbols, interval, period, retries, range_from, range_to, caller=caller)
                    for s in missing_symbols:
                        if s in yf_results:
                            results[s] = yf_results[s]
                for s in symbols:
                    if s not in results:
                        results[s] = MarketData(None, "UNKNOWN", None, False, False, "Missing")
                return results
            except Exception as e:
                logger.warning(f"Fyers batch fetch exception: {e}. Falling back to YFinance.")
        
        yf_fallback_results = self.yfinance_fetcher.get_batch_ohlcv(symbols, interval, period, retries, range_from, range_to, caller=caller)
        for s in symbols:
            if s not in yf_fallback_results:
                yf_fallback_results[s] = MarketData(None, "UNKNOWN", None, False, False, "Missing")
        return yf_fallback_results"""

content = content.replace(old_get_ohlcv, new_get_ohlcv)
content = content.replace(old_get_batch_ohlcv, new_get_batch_ohlcv)

with open("app/data_provider.py", "w") as f:
    f.write(content)
print("Replaced get_batch_ohlcv and get_ohlcv in AutoSwitchingFetcher")
