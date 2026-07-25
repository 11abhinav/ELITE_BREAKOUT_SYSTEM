import re

with open('docs/SYSTEM_ARCHITECTURE.md', 'r') as f:
    docs = f.read()

with open('app/config.py', 'r') as f:
    config_content = f.read()

# Update §7A.2
docs = re.sub(
    r'_MODE_CONFIG = \{\n.*?"REVERSAL": \(2.00,    1.00,       0.0100,     3.5\),\n\}',
    r'_MODE_CONFIG = {\n    "EOD":      (2.00,    0.80,       0.0075,     3.0),\n    "MULTI_TF": (1.50,    0.50,       0.0050,     3.0),\n    "REVERSAL": (2.00,    1.00,       0.0100,     3.5),\n    "PULLBACK": (2.00,    0.75,       0.0075,     3.0),\n}',
    docs, flags=re.DOTALL
)

# Update §7A.5
docs = re.sub(
    r'PARTIAL_EXIT = \{\n    "EOD":      \[40, 30, 30\],   .*?,\n    "REVERSAL": \[30, 30, 40\],   .*?,\n\}',
    'PARTIAL_EXIT = {\n    "EOD":      [40, 30, 30],   # T1: 40% exit, T2: 30% exit, T3: 30% hold\n    "REVERSAL": [30, 30, 40],   # T1: 30% exit, T2: 30% exit, T3: 40% hold (LTCG intent)\n    "MULTI_TF": [20, 30, 50],   # Aggressive partial\n    "PULLBACK": [40, 35, 25],   # Balanced partial\n}',
    docs, flags=re.DOTALL
)

# Update §7.6
docs = re.sub(
    r'Max alerts per run: SCANNER_MAX_ALERTS\["WEALTH"\] = 50',
    'Max alerts per run: SCANNER_MAX_ALERTS["WEALTH"] = 40',
    docs
)

# Update §18
start_str = '# 18. VERBATIM PRODUCTION CONFIGURATION (`app/config.py`)\n\nBelow is the verbatim source code of `app/config.py`:\n\n```python\n'
end_str = '\n```\n\n---\n\n# 19. DETERMINISTIC RECONSTRUCTION ANSWERS (Q1 – Q36)'

start_idx = docs.find(start_str)
end_idx = docs.find(end_str)

if start_idx != -1 and end_idx != -1:
    docs = docs[:start_idx + len(start_str)] + config_content + docs[end_idx:]

with open('docs/SYSTEM_ARCHITECTURE.md', 'w') as f:
    f.write(docs)

print("Updated docs successfully")
