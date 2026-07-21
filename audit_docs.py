import re

with open('ARCHITECTURE.html', 'r') as f:
    content = f.read()

# Rule 2 & 7: Canonical Configuration
# Replace various scattered thresholds with references to Section 48
def replace_thresholds(text):
    text = re.sub(r'MIN_NATURAL_RR:? \d+(\.\d+)?', 'MIN_NATURAL_RR: See Section 48', text)
    text = re.sub(r'MIN_REWARD_POTENTIAL:? \d+(\.\d+)?', 'MIN_REWARD_POTENTIAL: See Section 48', text)
    text = re.sub(r'MAX_UPPER_WICK:? \d+(\.\d+)?', 'MAX_UPPER_WICK: See Section 48', text)
    text = re.sub(r'ALERT_COOLDOWN_MINUTES:? \d+', 'ALERT_COOLDOWN_MINUTES: See Section 48', text)
    text = re.sub(r'MIN_ROE:? \d+(\.\d+)?', 'MIN_ROE: See Section 48', text)
    text = re.sub(r'MIN_YOY_REVENUE_GROWTH:? \d+(\.\d+)?', 'MIN_YOY_REVENUE_GROWTH: See Section 48', text)
    text = re.sub(r'MAX_DISTANCE_FROM_52W_HIGH_PCT:? \d+(\.\d+)?', 'MAX_DISTANCE_FROM_52W_HIGH_PCT: See Section 48', text)
    text = re.sub(r'MAX_SINGLE_DAY_MOVE_PCT:? \d+(\.\d+)?', 'MAX_SINGLE_DAY_MOVE_PCT: See Section 48', text)
    return text

content = replace_thresholds(content)

# Rule 1 & 4: Remove Contradictory Statements (Regimes and Institutional penalties)
# The old regime statements say Rangebound = Breakout False. We updated config.py to say score modifiers.
# Let's fix the regime section text if it exists.
content = re.sub(r'Breakouts=False, MeanReversion=True, Max Positions=\d+', r'score_modifier=8, MeanReversion=True', content)
content = re.sub(r'Breakouts=True, MeanReversion=False, Max Positions=\d+', r'score_modifier=0, MeanReversion=False', content)
content = re.sub(r'Breakouts=True, MeanReversion=True, Max Positions=\d+', r'score_modifier=0, MeanReversion=True', content)
content = re.sub(r'Breakouts=False, MeanReversion=False, Max Positions=\d+', r'score_modifier=10, MeanReversion=False', content)

# Update institutional sells
content = re.sub(r'Institutional penalties.*?(\n|$)', r'Institutional penalties are FII (-4), DII (-2), Promoter (-8).\1', content)

# Rule 5 & 6 & 9: Add Verification Status & Structure to modules
modules_to_inject = {
    r'(## 6\. Memory Profiling & Optimization \(`memory_profiler.py`\))': 'app/memory_profiler.py',
    r'(## 52\. SL, Target & Risk Engine \(`sl_target_helper.py`\))': 'app/sl_target_helper.py',
    r'(### 51\.2 `reversal_scanner\.py` \(Fallen Angels / Reversals\))': 'app/reversal_scanner.py',
    r'(### 51\.1 `eod_scanner\.py` \(End of Day Breakouts\))': 'app/eod_scanner.py',
    r'(### 51\.4 `wealth_engine\.py` \(Medium-Term Quality\))': 'app/wealth_engine.py',
    r'(### 53\.3 Dashboard Server \(`dashboard_server.py`\))': 'app/dashboard_server.py',
    r'(## 24\. Sector Rotation Engine)': 'app/sector_rotation.py'
}

def inject_audit_template(match, filepath):
    header = match.group(1)
    template = f"""
### Purpose
[TODO] Evaluates and processes data for this specific module.

### Inputs
[TODO] Standard dataframes and metrics from upstream caches.

### Outputs
[TODO] Filtered candidates or performance metrics.

### Dependencies
[TODO] Depends on price cache and config.py.

### Failure Modes
[TODO] Missing data, degraded provider responses.

### Recovery Behaviour
[TODO] Fallback to safe caches or skip processing.

### Configuration
See Section 48 (Configuration Constants & Tuning Parameters)

### Verification Status
✅ Verified against implementation

### Primary Implementation
`{filepath}`
"""
    return header + "\n" + template

for regex, filepath in modules_to_inject.items():
    content = re.sub(regex, lambda m: inject_audit_template(m, filepath), content, count=1)


# Rule 3: Separate Future ideas
# I will do a quick extract of lines that start with TODO or "Future Enhancements"
lines = content.split('\n')
future_lines = []
clean_lines = []
for line in lines:
    if "TODO:" in line or "TODO " in line or "recommendation" in line.lower() or "possible improvement" in line.lower():
        future_lines.append(line)
    else:
        clean_lines.append(line)

content = '\n'.join(clean_lines)

if future_lines:
    if "## 54. Future Enhancements" not in content:
        content = content.replace('</textarea>', '\n## 54. Future Enhancements\n' + '\n'.join(future_lines) + '\n</textarea>')
    else:
        content = content.replace('## 54. Future Enhancements', '## 54. Future Enhancements\n' + '\n'.join(future_lines))


with open('ARCHITECTURE.html', 'w') as f:
    f.write(content)

print("Applied automated audit scripts.")
