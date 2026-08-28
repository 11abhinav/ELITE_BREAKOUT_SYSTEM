import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "app"))
from app.multibagger import evaluate_multibagger_symbol

# Test Case 1: Missing shares (None)
try:
    fundamentals_none = {
        "symbol": "TEST1",
        "shares": None,
        "net_income_ttm": 100000,
        "total_equity": 500000
    }
    technicals = {"price": 100.0, "sma_50": 90.0, "sma_200": 80.0, "ema_20": 95.0, "volume": 1000}
    evaluate_multibagger_symbol("TEST1", fundamentals_none, technicals)
    print("Test 1 (shares=None): SUCCESS (No ZeroDivisionError)")
except Exception as e:
    print(f"Test 1 Failed: {type(e).__name__} - {e}")

# Test Case 2: Zero shares (0)
try:
    fundamentals_zero = {
        "symbol": "TEST2",
        "shares": 0,
        "net_income_ttm": 100000,
        "total_equity": 500000
    }
    evaluate_multibagger_symbol("TEST2", fundamentals_zero, technicals)
    print("Test 2 (shares=0): SUCCESS (No ZeroDivisionError)")
except Exception as e:
    print(f"Test 2 Failed: {type(e).__name__} - {e}")

