import json
from datetime import date, datetime
from app.database import get_all_scanner_health

def default(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return str(obj)

data = get_all_scanner_health()
rev = next((d for d in data if d["scanner_name"] == "REVERSAL"), None)
print(json.dumps(rev, default=default, indent=2))
