with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/dashboard_server.py', 'r') as f:
    content = f.read()

old_api = """@app.route("/api/breakout_watchlist", methods=["GET"])
def api_breakout_watchlist():
    \"\"\"Returns the live multi-tf breakout watchlist from the database.\"\"\"
    try:
        from database import get_active_breakout_watchlist
        data = get_active_breakout_watchlist()
        return jsonify({"status": "success", "data": data})"""

new_api = """@app.route("/api/breakout_watchlist", methods=["GET"])
def api_breakout_watchlist():
    \"\"\"Returns the live multi-tf breakout watchlist from the database.\"\"\"
    try:
        from database import get_active_breakout_watchlist
        data = get_active_breakout_watchlist()
        
        if data:
            try:
                import pandas as pd
                from price_cache import fetch_watchlist_data
                symbols = list(set([d["symbol"] for d in data]))
                wl_df = pd.DataFrame([{"Stock": s} for s in symbols])
                prices_data = fetch_watchlist_data(wl_df, period="5d", interval="1d")
                prices = {}
                for sym, df in prices_data.items():
                    if not df.empty:
                        prices[sym] = float(df["Close"].iloc[-1])
                for d in data:
                    d["cmp"] = prices.get(d["symbol"])
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to fetch CMP for watchlist: {e}")

        return jsonify({"status": "success", "data": data})"""

content = content.replace(old_api, new_api)

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/dashboard_server.py', 'w') as f:
    f.write(content)
