import re

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/database.py', 'r') as f:
    code = f.read()

correct_fn = """def close_position(symbol: str, exit_price: float, exit_signal: str = None) -> bool:
    \"\"\"Auto-close an open position when SELL signal detected.\"\"\"
    with _DB_WRITE_LOCK:
        try:
            with get_connection() as conn:
                success = False
                try:
                    with conn.cursor() as cur:
                        # Get the most recent OPEN position for this symbol
                        cur.execute(\"\"\"
                            SELECT id, alert_price FROM wealth_buy_alert 
                            WHERE symbol = %s AND is_closed = FALSE
                            ORDER BY alert_date DESC, alert_time DESC
                            LIMIT 1
                        \"\"\", (symbol,))
                        
                        result = cur.fetchone()
                        if not result:
                            logger.warning(f"⚠️  No open position found for {symbol}")
                            return False
                        
                        position_id, entry_price = result[0], result[1]
                        
                        # Calculate P&L
                        pnl_rs = exit_price - entry_price
                        pnl_pct = (pnl_rs / entry_price * 100) if entry_price else 0
                        
                        now = datetime.now(IST)
                        exit_date = now.strftime('%Y-%m-%d')
                        exit_time = now.strftime('%H:%M:%S')
                        
                        # Update position as closed
                        cur.execute(\"\"\"
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
                        \"\"\", (exit_price, exit_date, exit_time, exit_signal, pnl_rs, pnl_pct, position_id))
                        
                    conn.commit()
                    success = True
                    logger.info(f"💰 POSITION CLOSED: {symbol} at {exit_price} (P&L: {pnl_pct:.2f}%)")
                    insert_notification('sell', 'Position Closed', f'{symbol} closed at ₹{exit_price} ({exit_signal}). P&L: {pnl_pct:.2f}%', symbol)
                except Exception as inner_e:
                    logger.error(f"Failed to execute position close query: {inner_e}")
                    conn.rollback()
                return success
        except Exception as e:
            logger.error(f"❌ Failed to close position: {e}")
            return False"""

code = re.sub(r'def close_position\(symbol: str, exit_price: float, exit_signal: str = None\) -> bool:[\s\S]+?return False', correct_fn, code, count=1)

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/database.py', 'w') as f:
    f.write(code)

print("Restored close_position.")
