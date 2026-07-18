import os
import time
from app.config import WATCHLIST_PATH

json_path = os.path.join(os.path.dirname(WATCHLIST_PATH), "surveillance_blacklist.json")
print("JSON Path:", json_path)
if os.path.exists(json_path):
    file_age = time.time() - os.path.getmtime(json_path)
    print("File exists. Age in hours:", file_age / 3600)
else:
    print("File does NOT exist.")

try:
    from curl_cffi import requests as cffi_requests
    print("Testing curl_cffi connection to nseindia.com...")
    session = cffi_requests.Session(impersonate="chrome120")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com"
    }
    res = session.get("https://www.nseindia.com", headers=headers, timeout=8)
    print("Base URL Status:", res.status_code)
    
    asm_res = session.get("https://www.nseindia.com/api/reportASM", headers=headers, timeout=8)
    print("ASM URL Status:", asm_res.status_code)
except Exception as e:
    print("Error:", e)
