import re

files = [
    '/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/user_dashboard.html',
    '/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/admin_dashboard.html'
]

empty_data_js = """
  console.warn('Failed to load performance data, returning empty state.');
  return {
    generated_at: new Date().toISOString(),
    summary: {
      total_alerts: 0, judged: 0, winners: 0, losers: 0, open_positions: 0,
      win_rate: 0, avg_return_pct: 0, avg_win_pct: 0, avg_loss_pct: 0,
      best_trade_pct: 0, worst_trade_pct: 0, expectancy: 0, sl_triggered: 0, target_hit: 0,
    },
    trades: [],
    by_scanner: {}, by_category: {}, equity_curve: [], monthly: [],
  };
"""

for filepath in files:
    with open(filepath, 'r') as f:
        content = f.read()

    # Find loadData function
    load_data_pattern = re.compile(r"async function loadData\(\) \{.*?\n  \}\n  console\.warn\('Using demo data'\);\n  return getDemoData\(\);\n\}", re.DOTALL)
    
    match = load_data_pattern.search(content)
    if match:
        old_load_data = match.group(0)
        new_load_data = old_load_data.replace(
            "  console.warn('Using demo data');\n  return getDemoData();\n}",
            empty_data_js + "\n}"
        )
        content = content.replace(old_load_data, new_load_data)

    # Remove getDemoData function completely
    demo_data_pattern = re.compile(r"function getDemoData\(\) \{.*?return \{\n    generated_at:.*?monthly:\[\],\n  \};\n\}", re.DOTALL)
    match = demo_data_pattern.search(content)
    if match:
        content = content.replace(match.group(0), "")

    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Patched {filepath}")

