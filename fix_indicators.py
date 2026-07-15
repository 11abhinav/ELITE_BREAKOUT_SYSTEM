import re

files = ['app/eod_scanner.py', 'app/multi_tf_scanner.py']

safe_float_helper = """
def _safe_float(val, default=0.0):
    try:
        import pandas as pd
        if val is None or pd.isna(val) or val == "":
            return default
        return float(val)
    except:
        return default
"""

for path in files:
    with open(path, 'r') as f:
        content = f.read()

    # Add the helper at the top if not exists
    if "_safe_float(" not in content:
        # Find the first function definition and inject it right before
        first_def_idx = content.find("def ")
        content = content[:first_def_idx] + safe_float_helper + "\n" + content[first_def_idx:]

    # Replace float() calls with _safe_float()
    # e.g., float(latest["EMA20"]) -> _safe_float(latest.get("EMA20"))
    # e.g., float(latest.get("EMA20")) -> _safe_float(latest.get("EMA20"))
    
    content = re.sub(r'float\(latest\["(.*?)"\]\)', r'_safe_float(latest.get("\1"))', content)
    content = re.sub(r'float\(latest\.get\("(.*?)"\)\)', r'_safe_float(latest.get("\1"))', content)
    
    # same for ticker["Volume"].iloc[...]
    content = re.sub(r'float\(ticker\["(.*?)"\]\.iloc\[(.*?)\]\)', r'_safe_float(ticker["\1"].iloc[\2])', content)

    with open(path, 'w') as f:
        f.write(content)
        
print("Indicator patches complete.")
