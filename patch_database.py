import re

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/database.py', 'r') as f:
    code = f.read()

# 1. Add global_notifications schema
schema = """
                # Unified Notification Center
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS global_notifications (
                        id SERIAL PRIMARY KEY,
                        type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        message TEXT NOT NULL,
                        symbol TEXT,
                        is_seen BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                ''')
"""
if "global_notifications" not in code:
    code = code.replace("CREATE TABLE IF NOT EXISTS system_checkpoints", schema + "                CREATE TABLE IF NOT EXISTS system_checkpoints")

# 2. Add insert_notification
helper = """
def insert_notification(notif_type: str, title: str, message: str, symbol: str = None):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('''
                    INSERT INTO global_notifications (type, title, message, symbol)
                    VALUES (%s, %s, %s, %s)
                ''', (notif_type, title, message, symbol))
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to insert notification: {e}")

"""
if "def insert_notification" not in code:
    code = code.replace("def init_db():", helper + "def init_db():")

# 3. Hook into save_alert_if_new
hook_buy1 = """                    inserted = cur.rowcount > 0
                    if inserted:
                        insert_notification('buy', 'New Breakout Alert', f'{breakout_type} Breakout detected for {symbol} at ₹{entry_price}', symbol)
                    return inserted, capital_allocated, shares_bought"""
if "insert_notification('buy'" not in code:
    code = re.sub(r'return cur\.rowcount > 0, capital_allocated, shares_bought', hook_buy1, code)

# 4. Hook into save_wealth_buy_alert
hook_buy2 = """                        if cur.rowcount == 0:
                            logger.info(f"⏭️  BUY alert already saved today: {symbol} {breakout_type}")
                            return False  # Duplicate, skip
                        
                        insert_notification('buy', 'New Wealth Buy Alert', f'Wealth alert triggered for {symbol} at ₹{alert_price} ({breakout_type})', symbol)
"""
if "New Wealth Buy Alert" not in code:
    code = re.sub(r'if cur\.rowcount == 0:\s+logger\.info\([^)]+\)\s+return False\s+# Duplicate, skip', hook_buy2, code)

# 5. Hook into close_position
hook_sell = """                        cur.execute('''
                            UPDATE wealth_buy_alert 
                            SET is_closed = TRUE, 
                                exit_price = %s, 
                                exit_date = %s, 
                                exit_time = %s,
                                exit_signal = %s,
                                pnl_rs = %s,
                                pnl_pct = %s,
                                status = 'CLOSED'
                            WHERE id = %s
                        ''', (exit_price, exit_date, exit_time, exit_signal, pnl_rs, pnl_pct, position_id))
                    
                    conn.commit()
                    logger.info(f"💰 POSITION CLOSED: {symbol} at {exit_price} (P&L: {pnl_pct:.2f}%)")
                    insert_notification('sell', 'Position Closed', f'{symbol} closed at ₹{exit_price} ({exit_signal}). P&L: {pnl_pct:.2f}%', symbol)
                    return True"""

code = re.sub(r'UPDATE wealth_buy_alert\s+SET is_closed = TRUE,[\s\S]+?return True', hook_sell, code)

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/database.py', 'w') as f:
    f.write(code)

print("database.py patched.")
