import sys

with open("app/data_providers/fyers_fetcher.py", "r") as f:
    content = f.read()

if "from data_quality import DataQualityValidator, MarketData" not in content:
    content = content.replace("from core_enums import ProviderResult", 
        "from core_enums import ProviderResult\nfrom data_quality import DataQualityValidator, MarketData")

# fyers_fetcher.py has `def get_ohlcv` and `def get_batch_ohlcv`.
old_get_batch = """    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict:"""

new_get_batch = """    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, MarketData]:"""

# We need to change what `future.result()` returns. Since `get_ohlcv` will return `MarketData`, `results[orig_sym] = df` will just put `MarketData` there!
# But wait, we need to initialize `results.setdefault(s, None)` -> `results.setdefault(s, MarketData(None, "Fyers", None, False, False, "Missing"))`.

old_results = """                        # Map dataframe to all requested symbols mapping to this normalized symbol
                        for orig_sym in normalized_map[ns_sym]:
                            results[orig_sym] = df
                    except Exception as e:
                        logger.exception(f"Error fetching batch OHLCV for {ns_sym}")
                        for orig_sym in normalized_map[ns_sym]:
                            results[orig_sym] = None
            except concurrent.futures.TimeoutError:
                logger.error(f"Fyers batch fetch timed out after {calc_timeout}s. Cancelling remaining fetches.")
                # We can't actually cancel the threads cleanly in python 3.8 easily without cancel(), 
                # but the executor shutdown(wait=False) or leaving context handles it.
                pass
                        
        for s in symbols:
            results.setdefault(s, None)
                        
        return results"""

new_results = """                        # Map dataframe to all requested symbols mapping to this normalized symbol
                        for orig_sym in normalized_map[ns_sym]:
                            results[orig_sym] = df
                    except Exception as e:
                        logger.exception(f"Error fetching batch OHLCV for {ns_sym}")
                        for orig_sym in normalized_map[ns_sym]:
                            results[orig_sym] = MarketData(None, "Fyers", None, False, False, "Exception")
            except concurrent.futures.TimeoutError:
                logger.error(f"Fyers batch fetch timed out after {calc_timeout}s. Cancelling remaining fetches.")
                pass
                        
        for s in symbols:
            if s not in results:
                results[s] = MarketData(None, "Fyers", None, False, False, "Missing")
                        
        return results"""

content = content.replace(old_get_batch, new_get_batch)
content = content.replace(old_results, new_results)

# Now get_ohlcv
old_get_ohlcv = """    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None) -> pd.DataFrame:
        \"\"\"Fetch OHLCV data for a single symbol from Fyers.\"\"\"
        # [VERSION: NULL_POINTER_FIX_v1.0]
        if not symbol:
            return None
            
        # Check if Fyers circuit breaker is open (too many failures)
        if not _fyers_circuit_breaker.is_available():
            return None"""

new_get_ohlcv = """    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None) -> MarketData:
        \"\"\"Fetch OHLCV data for a single symbol from Fyers.\"\"\"
        # [VERSION: NULL_POINTER_FIX_v1.0]
        if not symbol:
            return MarketData(None, "UNKNOWN", None, False, False, "No symbol")
            
        # Check if Fyers circuit breaker is open (too many failures)
        if not _fyers_circuit_breaker.is_available():
            return MarketData(None, "Fyers", None, False, False, "Circuit Breaker Open")"""

# Also at the end of get_ohlcv, it returns pd.DataFrame.
old_return_df = """                # Reset index so 'Date' or 'Datetime' is a column
                df = df.reset_index()
                return df"""

new_return_df = """                # Reset index so 'Date' or 'Datetime' is a column
                df = df.reset_index()
                report = DataQualityValidator.validate(df, period, interval, range_from, range_to)
                if not report.is_valid:
                    return MarketData(None, "Fyers", report, False, False, "Quality Check Failed")
                return MarketData(df, "Fyers", report, False, False, None)"""

old_return_none = """                    time.sleep(random.uniform(0.5, 1.5))
        
        logger.error(f"❌ Exhausted retries fetching {ns_symbol} from Fyers")
        return None"""

new_return_none = """                    time.sleep(random.uniform(0.5, 1.5))
        
        logger.error(f"❌ Exhausted retries fetching {ns_symbol} from Fyers")
        return MarketData(None, "Fyers", None, False, False, "Exhausted retries")"""

content = content.replace(old_get_ohlcv, new_get_ohlcv)
content = content.replace(old_return_df, new_return_df)
content = content.replace(old_return_none, new_return_none)

with open("app/data_providers/fyers_fetcher.py", "w") as f:
    f.write(content)
print("Replaced fyers_fetcher.py")
