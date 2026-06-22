# Created by Copilot CLI: early yfinance tzcache bootstrap
import os
import logging

logger = logging.getLogger(__name__)

# Ensure app-local data tzcache exists and point yfinance to it before other modules import yfinance.
try:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    TZCACHE_DIR = os.path.join(BASE_DIR, "data", "tzcache")
    os.makedirs(TZCACHE_DIR, exist_ok=True)
    # Set XDG_CACHE_HOME so libraries using XDG respect our writable cache path
    os.environ.setdefault("XDG_CACHE_HOME", os.path.dirname(TZCACHE_DIR))
except Exception as e:
    logger.debug(f"Failed to prepare tzcache dir: {e}")

# Attempt to import yfinance and set its tz cache location safely.
try:
    import yfinance as yf
    try:
        yf.set_tz_cache_location(TZCACHE_DIR)
    except Exception:
        # Not fatal; avoid raising to callers. The goal is to avoid import-time errors.
        logger.debug("yfinance.set_tz_cache_location failed; proceeding")
        
    # Apply monkey-patch to yfinance to bypass "Invalid Crumb" and "Unauthorized" blocks
    import requests
    
    # Create a global custom session with a standard browser User-Agent
    custom_session = requests.Session()
    custom_session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive'
    })
    
    _orig_download = yf.download
    def patched_download(*args, **kwargs):
        if 'session' not in kwargs:
            kwargs['session'] = custom_session
        return _orig_download(*args, **kwargs)
    yf.download = patched_download

    _orig_ticker = yf.Ticker
    class PatchedTicker(yf.Ticker):
        def __init__(self, ticker, session=custom_session, *args, **kwargs):
            super().__init__(ticker, session=session, *args, **kwargs)
    yf.Ticker = PatchedTicker

except Exception as e:
    # Import may fail in rare environments; swallow to avoid crashing importers.
    logger.debug(f"yfinance import during bootstrap failed: {e}")
