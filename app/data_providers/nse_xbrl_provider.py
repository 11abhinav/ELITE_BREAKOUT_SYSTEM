import requests
import logging
from typing import List
from data_providers.fundamental_models import (
    RawFinancialRecord, ConsolidationType
)

logger = logging.getLogger(__name__)

class NseXbrlProvider:
    """
    Fetches raw corporate financial results from NSE India API.
    """
    
    def __init__(self):
        self.base_url = "https://www.nseindia.com/api/corporates-financial-results"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        self.session = requests.Session()
        self._initialized = False

    def _init_session(self):
        if not self._initialized:
            try:
                # establish cookies to bypass simple blocks
                self.session.get("https://www.nseindia.com", headers=self.headers, timeout=10)
                self._initialized = True
            except Exception as e:
                logger.error(f"Failed to initialize NSE session: {e}")

    def fetch_raw_financials(self, symbol: str) -> List[RawFinancialRecord]:
        self._init_session()
        
        headers = self.headers.copy()
        headers["Accept"] = "*/*"
        
        url = f"{self.base_url}?index=equities&symbol={symbol}"
        records = []
        
        try:
            res = self.session.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                for row in data:
                    # In a full implementation, parse the row data and map it to RawFinancialRecord
                    # Example mappings from NSE's format:
                    # row.get('revenue'), row.get('pat'), row.get('consolidated')
                    
                    # Stubbing the creation based on successful network test
                    rec = RawFinancialRecord(
                        symbol=symbol,
                        source="NSE_XBRL",
                        period_end_date=row.get('toDate', ''),
                        period_type="QUARTERLY", # Needs inference from 'period'
                        consolidation=ConsolidationType.CONSOLIDATED if row.get('consolidated') == 'Consolidated' else ConsolidationType.STANDALONE
                    )
                    records.append(rec)
            else:
                logger.warning(f"[{symbol}] NSE API returned {res.status_code}: {res.text[:200]}")
        except Exception as e:
            logger.error(f"[{symbol}] NSE XBRL fetch failed: {e}")
            
        return records
