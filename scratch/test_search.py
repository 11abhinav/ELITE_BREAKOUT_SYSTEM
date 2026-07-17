import requests

def search_yahoo_symbol(query):
    url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&quotesCount=3&country=India"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            quotes = r.json().get('quotes', [])
            for q in quotes:
                sym = q.get('symbol', '')
                if sym.endswith('.NS') or sym.endswith('.BO'):
                    return sym
    except Exception:
        pass
    return None

print(search_yahoo_symbol('ECORECO'))
print(search_yahoo_symbol('M&M'))
print(search_yahoo_symbol('TATAMOTORS'))
