import re

# Fix multibagger.py safe_float
with open('app/multibagger.py', 'r') as f:
    content = f.read()

old_safe_float = """def safe_float(val, default=0.0):
    try:
        import pandas as pd
        if val is None or pd.isna(val):
            return default
        return float(val)"""
        
new_safe_float = """def safe_float(val, default=0.0):
    try:
        import pandas as pd
        if val is None or pd.isna(val) or val == "":
            return default
        return float(val)"""
        
content = content.replace(old_safe_float, new_safe_float)

# Fix timezones in multibagger.py
content = re.sub(
    r"datetime\.now\(\)",
    r"datetime.now(pytz.timezone('Asia/Kolkata'))",
    content
)
if "import pytz" not in content:
    content = content.replace("from datetime import datetime, timedelta", "from datetime import datetime, timedelta\nimport pytz")

with open('app/multibagger.py', 'w') as f:
    f.write(content)


# Fix timezones in fyers_auth.py
with open('app/fyers_auth.py', 'r') as f:
    content = f.read()
    
content = re.sub(
    r"datetime\.now\(\)",
    r"datetime.now(pytz.timezone('Asia/Kolkata'))",
    content
)
if "import pytz" not in content:
    content = content.replace("import time", "import time\nimport pytz")

with open('app/fyers_auth.py', 'w') as f:
    f.write(content)


# Fix timezones in pledge_scraper.py
with open('app/pledge_scraper.py', 'r') as f:
    content = f.read()

content = re.sub(
    r"datetime\.now\(\)",
    r"datetime.now(pytz.timezone('Asia/Kolkata'))",
    content
)
if "import pytz" not in content:
    content = content.replace("import time", "import time\nimport pytz")

with open('app/pledge_scraper.py', 'w') as f:
    f.write(content)

print("Phase 2 Steps 1 & 2 Completed")
