import re

# 1. performance_tracker.py
pt_file = '/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/performance_tracker.py'
with open(pt_file, 'r') as f:
    pt_code = f.read()

# Make sure it uses IST
pt_code = re.sub(r'datetime\.now\(\)\.strftime', r'datetime.now(IST).strftime', pt_code)

with open(pt_file, 'w') as f:
    f.write(pt_code)

# 2. database.py
db_file = '/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/database.py'
with open(db_file, 'r') as f:
    db_code = f.read()

# Fix naive python datetimes
# Match `datetime.now()` but explicitly skip `datetime.now(IST)` or anything with args
db_code = re.sub(r'datetime\.now\(\)', r'datetime.now(IST)', db_code)

# Fix SQL `now()::TEXT` and `NOW()::TEXT`
# Be careful: `(now()::TEXT)` -> `((now() AT TIME ZONE 'Asia/Kolkata')::TEXT)`
db_code = re.sub(r'now\(\)::TEXT', r"(now() AT TIME ZONE 'Asia/Kolkata')::TEXT", db_code, flags=re.IGNORECASE)

with open(db_file, 'w') as f:
    f.write(db_code)

print("Done fixing IST everywhere")
