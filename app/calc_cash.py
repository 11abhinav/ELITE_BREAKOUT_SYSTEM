from database import get_connection, get_capital_info
import pandas as pd

with get_connection() as conn:
    df = pd.read_sql("SELECT * FROM alerts", conn)

cap_info = get_capital_info()
total_capital = cap_info.get("total_capital", 500000)

realized_pnl = df[(df['status'].isin(['WIN', 'LOSS'])) & (df['is_rejected'] == False)]['pnl_rs'].sum()
deployed_cap = df[(df['status'] == 'OPEN') & (df['is_rejected'] == False)]['capital_allocated'].sum()

print(f"Total Capital: {total_capital}")
print(f"Realized PnL: {realized_pnl}")
print(f"Total Equity: {total_capital + realized_pnl}")
print(f"Deployed Capital (OPEN): {deployed_cap}")
print(f"Cash in Hand (Available Margin): {total_capital + realized_pnl - deployed_cap}")

