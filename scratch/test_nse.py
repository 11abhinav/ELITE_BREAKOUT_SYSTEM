import requests
import pandas as pd
import io

def test_fetch():
    urls = {
        "Nifty 50": "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
        "Nifty Next 50": "https://nsearchives.nseindia.com/content/indices/ind_niftynext50list.csv",
        "Nifty Midcap 150": "https://nsearchives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
        "Nifty Smallcap 250": "https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap250list.csv"
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    }
    
    for name, url in urls.items():
        try:
            print(f"Fetching {name} from {url}...")
            response = requests.get(url, headers=headers, timeout=15)
            print(f"Status Code: {response.status_code}")
            if response.status_code == 200:
                df = pd.read_csv(io.StringIO(response.text))
                print(f"Columns: {list(df.columns)}")
                print(f"First 3 rows:\n{df.head(3)}\n")
            else:
                print(f"Response: {response.text[:200]}")
        except Exception as e:
            print(f"Error fetching {name}: {e}")

if __name__ == "__main__":
    test_fetch()
