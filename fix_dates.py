import re

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/dashboard_server.py', 'r') as f:
    content = f.read()

helper = """
def serialize_datetimes(obj):
    if isinstance(obj, dict):
        return {k: serialize_datetimes(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_datetimes(i) for i in obj]
    elif isinstance(obj, datetime):
        return obj.astimezone(IST).isoformat()
    return obj
"""

if "def serialize_datetimes" not in content:
    content = content.replace('IST = ZoneInfo("Asia/Kolkata")', 'IST = ZoneInfo("Asia/Kolkata")\n' + helper)

# Replace jsonify(rows) in fetch_errors
content = re.sub(r'return jsonify\(rows\)', r'return jsonify(serialize_datetimes(rows))', content)

# Replace jsonify(result) in scanner_status
content = re.sub(r'return jsonify\(result\)', r'return jsonify(serialize_datetimes(result))', content)

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/dashboard_server.py', 'w') as f:
    f.write(content)

print("Done fixing datetimes")
