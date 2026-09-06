import os, sys
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd

from price_cache import fetch_unified_historical, get_cached_df
from market_utils import get_expected_latest_closed_daily_bar, get_expected_latest_trading_date

IST = ZoneInfo("Asia/Kolkata")
now_ist = datetime.now(IST)
expected_closed = get_expected_latest_closed_daily_bar(now_ist)

test_symbols = [
    "ADANIPOWER", "CYIENT", "RTNPOWER", "RPOWER", "CPPLUS", 
    "ENTERO", "ACUTAAS", "BBOX", "CEMPRO", "RBA"
]

rows_before_map = {}
last_before_map = {}

for sym in test_symbols:
    df_before = get_cached_df(sym, interval="1d", period="1y")
    rows_before = len(df_before) if df_before is not None else 0
    last_before = "NONE"
    if df_before is not None and not df_before.empty:
        c = 'Date' if 'Date' in df_before.columns else ('Datetime' if 'Datetime' in df_before.columns else None)
        if c:
            last_before = str(pd.to_datetime(df_before[c].iloc[-1]).date())
        elif isinstance(df_before.index, pd.DatetimeIndex):
            last_before = str(df_before.index[-1].date())
    rows_before_map[sym] = rows_before
    last_before_map[sym] = last_before

# Batch fetch all together concurrently
print(f"Batch fetching {len(test_symbols)} symbols concurrently...", flush=True)
fetched_dict = fetch_unified_historical(test_symbols, period="1y", interval="1d", requester="DIAGNOSTIC_AUDIT")

log_lines = []
log_lines.append(f"Current IST: {now_ist.isoformat()}")
log_lines.append(f"Expected latest closed daily bar: {expected_closed}")
log_lines.append("=" * 115)
log_lines.append(f"{'SYMBOL':<12} | {'ROWS_BEF':<8} | {'LAST_DATE_BEF':<14} | {'FETCH_STATUS':<12} | {'ROWS_AFT':<8} | {'LAST_DATE_AFT':<14} | {'IS_FRESH':<8} | {'ACTION':<15}")
log_lines.append("-" * 115)

for sym in test_symbols:
    rows_before = rows_before_map[sym]
    last_before = last_before_map[sym]

    df_after = fetched_dict.get(sym)
    rows_after = len(df_after) if df_after is not None else 0
    last_after = "NONE"
    if df_after is not None and not df_after.empty:
        c = 'Date' if 'Date' in df_after.columns else ('Datetime' if 'Datetime' in df_after.columns else None)
        if c:
            last_after = str(pd.to_datetime(df_after[c].iloc[-1]).date())
        elif isinstance(df_after.index, pd.DatetimeIndex):
            last_after = str(df_after.index[-1].date())

    is_fresh = (last_after >= str(expected_closed)) if last_after != "NONE" else False
    fetch_status = "SUCCESS" if rows_after > 0 else "FAILED"
    action = "REFRESHED" if (is_fresh and rows_after > rows_before) else ("ACCEPTED_STALE" if not is_fresh else "UP_TO_DATE")

    line = f"{sym:<12} | {rows_before:>8} | {last_before:<14} | {fetch_status:<12} | {rows_after:>8} | {last_after:<14} | {str(is_fresh):<8} | {action:<15}"
    log_lines.append(line)
    print(line, flush=True)

log_lines.append("=" * 115)

out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audit_diag_result.txt")
with open(out_path, "w") as f:
    f.write("\n".join(log_lines) + "\n")

print(f"WROTE {out_path} successfully", flush=True)
