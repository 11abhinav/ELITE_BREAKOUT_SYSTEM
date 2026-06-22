with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/database.py', 'r') as f:
    content = f.read()

old_query = """            cur.execute("SELECT entry_price, stop_loss, score, capital_allocated FROM alerts WHERE id = %s", (alert_id,))
            row = cur.fetchone()
            if not row:
                return False
            
            entry_price, stop_loss, score, old_cap = row
            old_cap = float(old_cap) if old_cap else 0.0"""

new_query = """            cur.execute("SELECT entry_price, stop_loss, score, capital_allocated, status, exit_price FROM alerts WHERE id = %s", (alert_id,))
            row = cur.fetchone()
            if not row:
                return False
            
            entry_price, stop_loss, score, old_cap, status, exit_price = row
            old_cap = float(old_cap) if old_cap else 0.0"""

old_update = """            # Update the alert with the newly calculated amounts
            cur.execute(
                "UPDATE alerts SET capital_allocated = %s, shares_bought = %s WHERE id = %s",
                (new_cap, new_shares, alert_id)
            )"""

new_update = """            # Update the alert with the newly calculated amounts
            cur.execute(
                "UPDATE alerts SET capital_allocated = %s, shares_bought = %s WHERE id = %s",
                (new_cap, new_shares, alert_id)
            )
            
            # If the trade is already closed (WIN/LOSS), retroactively fix its realized PnL in Rupees
            if status in ('WIN', 'LOSS') and exit_price is not None:
                new_pnl_rs = new_shares * (exit_price - entry_price)
                cur.execute("UPDATE alerts SET pnl_rs = %s WHERE id = %s", (new_pnl_rs, alert_id))"""

content = content.replace(old_query, new_query)
content = content.replace(old_update, new_update)

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/database.py', 'w') as f:
    f.write(content)
