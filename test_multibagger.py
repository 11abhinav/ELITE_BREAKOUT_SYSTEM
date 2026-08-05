import sys
from datetime import datetime, date
from app.multibagger import _is_fundamental_cache_fresh

test_data = [
    {"date": str(date.today())},
    {"fetched_at": datetime.now().isoformat()},
    {"date": "2020-01-01"},
    {}
]

for d in test_data:
    print(f"Testing {d}: {_is_fundamental_cache_fresh(d)}")
