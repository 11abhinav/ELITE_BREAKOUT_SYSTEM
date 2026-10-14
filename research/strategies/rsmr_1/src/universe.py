"""
Historical Universe & Market Data Loader for RSMR-1.
Loads certified Upstox 1D historical data and resolves sector classifications.
"""

import os
import glob
import logging
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger("RSMR_1_Universe")

class UniverseManager:
    def __init__(self, data_dir: str = "data/history/1d"):
        self.data_dir = data_dir
        self.stocks_data: Dict[str, pd.DataFrame] = {}
        self.sector_map: Dict[str, str] = {}
        self.calendar: List[pd.Timestamp] = []
        self._load_sector_mappings()
        self._load_all_parquet()

    def _load_sector_mappings(self):
        try:
            from sector_rotation import NSE_SECTOR_MAP
            self.sector_map = dict(NSE_SECTOR_MAP)
        except Exception:
            # Fallback path if import path differs
            try:
                import sys
                sys.path.insert(0, os.path.abspath("app"))
                from sector_rotation import NSE_SECTOR_MAP
                self.sector_map = dict(NSE_SECTOR_MAP)
            except Exception as e:
                logger.warning(f"Failed to import NSE_SECTOR_MAP: {e}")
                self.sector_map = {}

    def _load_all_parquet(self):
        files = sorted(glob.glob(os.path.join(self.data_dir, "*.parquet")))
        logger.info(f"Loading {len(files)} 1D parquet files from {self.data_dir}...")
        
        all_dates_set = set()
        
        for f in files:
            sym = os.path.basename(f).replace(".parquet", "")
            if sym.startswith(".") or "NIFTY" in sym.upper() or "INDEX" in sym.upper():
                continue
            try:
                df = pd.read_parquet(f)
                # Normalize column names to title case
                col_map = {c: c.capitalize() for c in df.columns}
                df = df.rename(columns=col_map)
                
                date_col = "Date" if "Date" in df.columns else ("Datetime" if "Datetime" in df.columns else df.columns[0])
                df["Dt"] = pd.to_datetime(df[date_col]).dt.tz_localize(None).dt.normalize()
                
                # Filter valid price bars
                df = df.dropna(subset=["Close", "Dt"]).drop_duplicates("Dt").sort_values("Dt")
                df = df[df["Close"] > 0]
                
                if len(df) < 50:
                    continue
                    
                df = df.set_index("Dt")
                self.stocks_data[sym] = df
                all_dates_set.update(df.index)
            except Exception as e:
                logger.debug(f"Error loading {f}: {e}")
                
        self.calendar = sorted(list(all_dates_set))
        logger.info(f"Loaded {len(self.stocks_data)} valid stocks. Calendar span: {self.calendar[0].date()} to {self.calendar[-1].date()} ({len(self.calendar)} sessions).")

    def get_sector(self, symbol: str) -> str:
        clean = symbol.upper().replace(".NS", "").replace(".BO", "")
        return self.sector_map.get(clean, "OTHERS")

    def get_quarterly_rebalance_dates(self) -> List[pd.Timestamp]:
        """
        Finds the last valid NSE trading session of each calendar quarter.
        Quarters end in March (3), June (6), September (9), December (12).
        """
        rebalance_dates = []
        cal_series = pd.Series(self.calendar)
        
        # Group by Year and Quarter
        year_quarters = cal_series.groupby([cal_series.dt.year, cal_series.dt.quarter])
        for (year, quarter), group in year_quarters:
            # Last available session of the quarter
            last_date = group.max()
            rebalance_dates.append(last_date)
            
        return sorted(rebalance_dates)

    def get_next_trading_day(self, current_date: pd.Timestamp) -> Optional[pd.Timestamp]:
        idx = np.searchsorted(self.calendar, current_date, side="right")
        if idx < len(self.calendar):
            return self.calendar[idx]
        return None
