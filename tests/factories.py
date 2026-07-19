import pandas as pd
from typing import Optional, Dict, Any, List
from datetime import datetime
from app.validation.result import ValidationResult
from app.validation.context import ValidationContext

class PriceHistoryBuilder:
    def __init__(self, symbol: str = "TEST.NS"):
        self.symbol = symbol
        self.periods = 100
        self.freq = "D"
        self.start_date = "2023-01-01"
        self.base_price = 100.0
        self.volatility = 2.0
        self.volume = 100_000
        self.modifications: Dict[str, Dict[str, Any]] = {}
        
    def with_periods(self, periods: int) -> 'PriceHistoryBuilder':
        self.periods = periods
        return self
        
    def with_start_date(self, start_date: str) -> 'PriceHistoryBuilder':
        self.start_date = start_date
        return self
        
    def with_base_price(self, price: float) -> 'PriceHistoryBuilder':
        self.base_price = price
        return self
        
    def with_spike(self, date_str: str, high: float, volume: int) -> 'PriceHistoryBuilder':
        self.modifications[date_str] = {"High": high, "Volume": volume}
        return self
        
    def build(self) -> pd.DataFrame:
        dates = pd.date_range(self.start_date, periods=self.periods, freq=self.freq)
        df = pd.DataFrame({
            "Open": [self.base_price] * self.periods,
            "High": [self.base_price + self.volatility] * self.periods,
            "Low": [self.base_price - self.volatility] * self.periods,
            "Close": [self.base_price] * self.periods,
            "Volume": [self.volume] * self.periods
        }, index=dates)
        
        # Apply specific date modifications (e.g. for breakout days)
        for date_str, mods in self.modifications.items():
            dt = pd.to_datetime(date_str)
            if dt in df.index:
                for col, val in mods.items():
                    df.loc[dt, col] = val
                    
        return df

class CandidateBuilder:
    def __init__(self, symbol: str = "TEST.NS"):
        self.data = {
            "symbol": symbol,
            "close": 105.0,
            "volume_ratio": 1.5,
            "is_breakout": True,
            "trend_status": "UP",
            "score": 85
        }
        
    def with_volume_ratio(self, ratio: float) -> 'CandidateBuilder':
        self.data["volume_ratio"] = ratio
        return self
        
    def with_breakout(self, is_breakout: bool) -> 'CandidateBuilder':
        self.data["is_breakout"] = is_breakout
        return self
        
    def with_score(self, score: int) -> 'CandidateBuilder':
        self.data["score"] = score
        return self
        
    def with_trend(self, trend: str) -> 'CandidateBuilder':
        self.data["trend_status"] = trend
        return self
        
    def build(self) -> Dict[str, Any]:
        # Return a copy to ensure immutability
        return dict(self.data)

class ValidationResultBuilder:
    def __init__(self):
        self.result = ValidationResult()
        
    def with_failure(self, code, message: str) -> 'ValidationResultBuilder':
        self.result.add_failure(code, message)
        return self
        
    def with_score(self, score: int) -> 'ValidationResultBuilder':
        self.result.metrics.quality_score = score
        return self
        
    def build(self) -> ValidationResult:
        import copy
        return copy.deepcopy(self.result)

class OpportunityBuilder:
    def __init__(self, symbol: str = "TEST.NS"):
        self.data = {
            "symbol": symbol,
            "entry_price": 105.0,
            "stop_loss": 95.0,
            "target": 125.0,
            "score": 85,
            "setup_type": "BREAKOUT",
            "risk_reward": 2.0
        }
        
    def with_stop_loss(self, sl: float) -> 'OpportunityBuilder':
        self.data["stop_loss"] = sl
        if self.data["entry_price"] > sl:
            self.data["risk_reward"] = (self.data["target"] - self.data["entry_price"]) / (self.data["entry_price"] - sl)
        return self
        
    def with_target(self, target: float) -> 'OpportunityBuilder':
        self.data["target"] = target
        if self.data["entry_price"] > self.data["stop_loss"]:
            self.data["risk_reward"] = (target - self.data["entry_price"]) / (self.data["entry_price"] - self.data["stop_loss"])
        return self
        
    def build(self) -> Dict[str, Any]:
        return dict(self.data)

class AlertBuilder:
    def __init__(self, symbol: str = "TEST.NS"):
        self.data = {
            "symbol": symbol,
            "message": f"Breakout alert for {symbol}",
            "timestamp": datetime.now().isoformat(),
            "priority": "HIGH"
        }
        
    def with_priority(self, priority: str) -> 'AlertBuilder':
        self.data["priority"] = priority
        return self
        
    def build(self) -> Dict[str, Any]:
        return dict(self.data)

# Factory helper functions for cleaner imports
def make_price_history(symbol: str = "TEST.NS") -> PriceHistoryBuilder:
    return PriceHistoryBuilder(symbol)

def make_candidate(symbol: str = "TEST.NS") -> CandidateBuilder:
    return CandidateBuilder(symbol)

def make_opportunity(symbol: str = "TEST.NS") -> OpportunityBuilder:
    return OpportunityBuilder(symbol)
    
def make_alert(symbol: str = "TEST.NS") -> AlertBuilder:
    return AlertBuilder(symbol)

def make_validation_result() -> ValidationResultBuilder:
    return ValidationResultBuilder()
