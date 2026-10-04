import os, sys, requests
sys.path.insert(0, ".")
sys.path.insert(0, "app")

token = os.getenv("UPSTOX_ACCESS_TOKEN")
if not token and os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if line.startswith("UPSTOX_ACCESS_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

from app.data_providers.fundamental_source_router import FundamentalSourceRouter
router = FundamentalSourceRouter()

val_symbols = [
    "ARIS", "BAYERCROP", "BHARATWIRE", "CUPID", "DPABHUSHAN",
    "GUJTHEM", "HUHTAMAKI", "JUSTDIAL", "KIRIINDUS", "KSL",
    "MOIL", "PATANJALI", "SIGNATURE", "GUJGASLTD", "FRONTSP",
    "GOCLCORP", "GODREJPROP", "MAHLIFE", "MARSONS", "PFIZER"
]

headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
for sym in val_symbols:
    isin = router._resolve_isin(sym)
    if not isin:
        print(f"{sym:12} -> NO ISIN")
        continue
    url = f"https://api.upstox.com/v2/fundamentals/{isin}/key-ratios"
    res = requests.get(url, headers=headers, timeout=10)
    if res.status_code == 200:
        data = res.json().get("data", [])
        ev_item = next((it for it in data if "EV/EBITDA" in it.get("name", "")), None)
        pe_item = next((it for it in data if it.get("name") == "P/E"), None)
        ev_val = ev_item.get("company_value") if ev_item else None
        pe_val = pe_item.get("company_value") if pe_item else None
        print(f"{sym:12} ({isin}) -> EV/EBITDA: {ev_val} | P/E: {pe_val}")
    else:
        print(f"{sym:12} ({isin}) -> HTTP {res.status_code}: {res.text[:60]}")
