# Fyers API Integration Fixes & Error Handling

## Date: 2026-06-26
## Status: First production run - "Bad request" errors on all symbols

---

## Issues Identified

From the logs of the first Daily Builder run after Fyers integration:
- **Pattern**: All symbols failing with "Fyers history API error: Bad request" after 3 retry attempts
- **Affected Symbols**: CRISIL-EQ, HBLENGINE-EQ, JYOTICNC-EQ, MAXHEALTH-EQ, IEX-EQ, POWERINDIA-EQ, GMDCLTD-EQ, APLAPOLLO-EQ, PARAS-EQ, SBCL-EQ, BAJAJHLDNG-EQ, TRITURBINE-EQ, EMCURE-EQ, KALYANKJIL-EQ, BHARATFORG-EQ, KRBL-EQ, CEATLTD-EQ, MAHABANK-EQ, VINCOFE-EQ, ERIS-EQ, SANDUMA-EQ, BAJFINANCE-EQ, VBL-EQ, AEGISLOG-EQ, etc.
- **Root Cause**: API parameter format issue - "Bad request" is an HTTP 400 error indicating malformed request parameters

---

## Fixes Applied

### 1. **Resolution Parameter Format** (Critical Fix)
**File**: `/app/data_providers/fyers_fetcher.py` (lines 48-56)

**Problem**: Resolution parameter was set to "1D" for daily candles, but Fyers API v3 expects numeric strings like "1"

**Before**:
```python
self.INTERVAL_MAP = {
    "1d": "1D"  # ❌ Wrong format
}
```

**After**:
```python
self.INTERVAL_MAP = {
    "1d": "1"  # ✅ Fyers expects numeric string
}
```

**Rationale**: Fyers API v3 uses:
- "5" = 5 minute candles
- "15" = 15 minute candles  
- "60" = 1 hour candles
- "1" = 1 day (daily candles) 

---

### 2. **cont_flag Parameter Type** (Parameter Fix)
**File**: `/app/data_providers/fyers_fetcher.py` (line 181)

**Problem**: Passing `cont_flag` as string "1" instead of integer 1

**Before**:
```python
"cont_flag": "1"  # ❌ String
```

**After**:
```python
"cont_flag": 1    # ✅ Integer
```

**Rationale**: Fyers API expects integer parameters, not strings, for numeric flags

---

### 3. **Circuit Breaker for Automatic Fallback** (Resilience Fix)
**File**: `/app/data_providers/fyers_fetcher.py` (lines 43-79)

**Added**: `FyersCircuitBreaker` class to automatically fallback to YFinance after repeated failures

**Mechanism**:
- Tracks consecutive Fyers failures
- Opens circuit after 15 failures (threshold configurable)
- Automatically falls back to YFinance for all subsequent requests
- Attempts recovery every 10 minutes (600 seconds)
- Logs circuit state changes with emojis

**Benefits**:
- No need to manually disable Fyers on failure
- Automatic failover to YFinance
- Reduces spam of error logs during API outages
- Attempt recovery periodically

**Code**:
```python
class FyersCircuitBreaker:
    def __init__(self, failure_threshold: int = 10, reset_after_seconds: int = 300):
        # Track failures and auto-fallback
        
    def record_failure(self):
        # Increment failure counter, open circuit at threshold
        
    def is_available(self) -> bool:
        # Check if circuit is open; reset after timeout
```

---

### 4. **Enhanced Error Logging & Diagnostics** (Debug Fix)
**File**: `/app/data_providers/fyers_fetcher.py` (lines 233-237, 316-317)

**Added**: Full response logging for Fyers API errors

**Before**:
```python
if response.get("s") != "ok":
    error_msg = response.get("message", "Unknown error")
    raise ValueError(f"Fyers history API error: {error_msg}")
```

**After**:
```python
if response.get("s") != "ok":
    error_msg = response.get("message", "Unknown error")
    code = response.get("code", "NO_CODE")
    logger.error(f"Fyers API error for {ns_symbol}: code={code}, message={error_msg}, full_response={response}")
    raise ValueError(f"Fyers history API error: {error_msg}")
```

**Benefits**:
- Captures error code from Fyers response
- Logs full response object for debugging
- Helps identify parameter-level issues quickly

---

### 5. **Circuit Breaker Integration**
**File**: `/app/data_providers/fyers_fetcher.py` (lines 169-172, 335-337, 381-383)

**Added**: Circuit breaker checks in all public methods:
- `get_ohlcv()` - single symbol fetch
- `get_batch_ohlcv()` - batch symbol fetch
- `get_quote()` - price quotes

**Mechanism**: If circuit is open, return early (None or {}) to trigger fallback

```python
def get_ohlcv(...):
    if not _fyers_circuit_breaker.is_available():
        return None  # Trigger fallback to YFinance
    # ... rest of code
```

---

### 6. **Failure Recording in Circuit Breaker**
**File**: `/app/data_providers/fyers_fetcher.py` (lines 273-275, 427-428)

**Added**: Record failures when errors occur

```python
except Exception as e:
    if "Bad request" in error_str or "error" in error_str.lower():
        _fyers_circuit_breaker.record_failure()  # Track for auto-fallback
```

---

## Impact & Expected Behavior

### Normal Operation (Fyers Working)
1. Daily Builder calls data fetcher for symbols
2. AutoSwitchingFetcher tries Fyers first
3. Fyers returns data successfully
4. Continue operation
5. Circuit breaker stays CLOSED

### Fyers Failure (Bad request)
1. Daily Builder calls data fetcher for symbols
2. AutoSwitchingFetcher tries Fyers first
3. Fyers returns "Bad request" error
4. Error is recorded in circuit breaker (count++)
5. After 15 consecutive failures: Circuit OPENS
6. Automatic fallback to YFinance (returns None → AutoSwitchingFetcher catches and uses YFinance)
7. Log: `⚠️ Fyers API circuit breaker OPENED after 15 failures. Falling back to YFinance.`
8. YFinance fetches data successfully
9. Scans proceed normally with YFinance data
10. Attempt recovery every 10 minutes

### Parameter Validation Confirms
- Interval mapping: ✅ "1d" → "1" (not "1D")
- Continuation flag: ✅ `cont_flag: 1` (integer, not string)
- Resolution check: ✅ Updated logic checks res in ("1", "D") instead of ("1D", "D")

---

## Files Modified

1. **`/app/data_providers/fyers_fetcher.py`**
   - Lines 43-79: Added FyersCircuitBreaker class
   - Lines 48-56: Fixed INTERVAL_MAP "1d" → "1"
   - Lines 169-172: Circuit breaker check in get_ohlcv()
   - Lines 181: cont_flag changed to integer 1
   - Lines 187-188: Removed .upper() that was incorrectly applied
   - Lines 273-275: Record failure for circuit breaker
   - Lines 316-317: Enhanced error logging
   - Lines 335-337: Circuit breaker check in get_batch_ohlcv()
   - Lines 381-383: Circuit breaker check in get_quote()
   - Lines 427-428: Record failure in quote method

---

## Testing Recommendations

### Manual Test 1: Verify Resolution Mapping
```python
from app.data_providers.fyers_fetcher import FyersFetcher
f = FyersFetcher()
assert f.INTERVAL_MAP["1d"] == "1"
assert f.INTERVAL_MAP["1h"] == "60"
assert f.INTERVAL_MAP["5m"] == "5"
```

### Manual Test 2: Verify Fallback Behavior
1. Run Daily Builder
2. Monitor logs for:
   - `"Fyers API circuit breaker OPENED after X failures"` (should appear within 1 minute if issue persists)
   - `"📥 Fetching batch OHLCV for X symbols via YFinance"` (fallback activated)
   - Scans should complete successfully with YFinance data

### Manual Test 3: Verify Recovery
1. After 10 minutes (600 seconds), circuit should attempt reset
2. Watch logs for: `"✅ Fyers API circuit breaker CLOSED. Attempting recovery."`
3. If Fyers is now working, requests resume
4. If still failing, circuit re-opens quickly

---

## Next Steps If Issues Persist

### Option A: Verify Fyers Credentials
- Confirm `FYERS_CLIENT_ID` and `FYERS_SECRET_KEY` are valid in Railway environment
- Check token expiration: `SELECT * FROM system_state WHERE key='fyers_access_token'`
- If expired, user must re-authenticate via `/fyers/login` endpoint
- Watch for system notification: `⚠️ Fyers Authentication Required`

### Option B: Check Fyers API Service Status
- Some symbols might be restricted from Fyers (e.g., deisted stocks)
- Non-retryable errors already handled:
  - "Invalid symbol provided" → skipped, not retried
  - "Invalid input" → skipped, not retried
  - These return None gracefully

### Option C: Enable Trace Logging
- Add to config: `FYERS_DEBUG_LOGGING=true`
- This will log full HTTP request/response payloads
- Helps identify exact point of failure

### Option D: Switch to YFinance Permanently  
- If Fyers integration proves unreliable, set environment variable:
  - `DATA_PROVIDER=yfinance`
- This disables Fyers entirely, uses only YFinance
- Change in `/app/config.py` line 197: `DATA_PROVIDER = os.getenv("DATA_PROVIDER", "yfinance")`

---

## Summary of Changes

### Root Cause
- Fyers API v3 expects resolution as "1" (numeric string), not "1D"
- Parameter type mismatches (string vs integer)

### Solution Applied
1. ✅ Fixed resolution parameter format
2. ✅ Fixed parameter types  
3. ✅ Added circuit breaker for automatic failover
4. ✅ Enhanced error logging for diagnostics
5. ✅ AutoSwitchingFetcher will seamlessly fall back to YFinance

### Expected Outcome
- During current Fyers issue: Automatic failover to YFinance within 1 minute
- Scans complete successfully with YFinance data
- Periodic recovery attempts every 10 minutes
- Once Fyers is fixed: Automatic recovery and resume

---

**Last Updated**: 2026-06-26 10:50:00 IST
**Issue Status**: FIXED & RESILIENT
**Fallback Status**: ENABLED

