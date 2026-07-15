import re

with open('app/wealth_engine.py', 'r') as f:
    content = f.read()

# 1. Update _safe_float logic
old_safe_float = """    def _safe_float(val, default=0.0):
        if val is None or pd.isna(val): return default
        try: return float(val)
        except: return default"""
        
new_safe_float = """    def _safe_float(val, default=0.0):
        if val is None or pd.isna(val) or val == "": return default
        try: return float(val)
        except: return default"""
        
content = content.replace(old_safe_float, new_safe_float)

# 2. Replace raw float() calls inside _compute_metrics and entry checks
content = re.sub(r'float\(last_row\[\'Close\'\]\)', '_safe_num(last_row.get(\'Close\'))', content)
content = re.sub(r'float\(last_row\[\'ATR\'\]\)', '_safe_num(last_row.get(\'ATR\'))', content)
content = re.sub(r'float\(hist\[\'High\'\]\.max\(\)\)', '_safe_num(hist[\'High\'].max())', content)
content = re.sub(r'float\((last_row\[.*?\])\)', r'_safe_num(\1)', content)
content = re.sub(r'float\(p_row\.get\((.*?)\)\)', r'_safe_num(p_row.get(\1))', content)

with open('app/wealth_engine.py', 'w') as f:
    f.write(content)
