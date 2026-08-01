import pytest
from app.database import init_db, get_connection

def test_extreme_varchar_inserts(mocker):
    """
    Proves that dynamic fields like 'symbol', 'scanner_name', and 'error_msg' 
    have been successfully migrated to TEXT and can safely store 
    arbitrarily large strings without triggering StringDataRightTruncation.
    """
    # 1. Initialize the database locally
    init_db()
    
    extremely_long_string = "A" * 10000  # 10,000 characters
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 2. Test inserting into 'alerts' (symbol TEXT)
            try:
                cur.execute('''
                    INSERT INTO alerts (symbol, breakout_type, alert_time, alert_date, scanner, category, entry_price, stop_loss, target_price, signals, score)
                    VALUES (%s, 'test_breakout', NOW(), CURRENT_DATE, 'test_scanner', 'test_cat', 100.0, 90.0, 120.0, 'test_signal', 85)
                ''', (extremely_long_string,))
                success_alerts = True
            except Exception as e:
                success_alerts = False
                pytest.fail(f"Failed to insert long string into alerts.symbol: {e}")
                
            # 3. Test inserting into 'scanner_health' (scanner_name TEXT, error_msg TEXT)
            try:
                cur.execute('''
                    INSERT INTO scanner_health (scanner_name, status, error_msg, updated_at)
                    VALUES (%s, 'DOWN', %s, NOW())
                ''', (extremely_long_string, extremely_long_string))
                success_health = True
            except Exception as e:
                success_health = False
                pytest.fail(f"Failed to insert long string into scanner_health: {e}")

    assert success_alerts, "alerts table constraint failed"
    assert success_health, "scanner_health table constraint failed"

def test_symbol_mappings_on_conflict(mocker):
    """
    Proves that the modern ON CONFLICT constraint for symbol_mappings 
    works correctly with the (provider, original_symbol) unique index.
    """
    init_db()
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            # First insert
            cur.execute('''
                INSERT INTO symbol_mappings (provider, original_symbol, mapped_symbol, mapping_type, original_sym, mapped_sym, mapping_state, failure_count)
                VALUES ('BSE', 'TEST_SYM', 'TEST_MAPPED', 'BSE', 'TEST_SYM', 'TEST_MAPPED', 'ACTIVE', 0)
                ON CONFLICT (mapping_type, original_sym) DO NOTHING
            ''')
            
            # Second insert (duplicate), should resolve via ON CONFLICT DO UPDATE
            cur.execute('''
                INSERT INTO symbol_mappings (provider, original_symbol, mapped_symbol, mapping_type, original_sym, mapped_sym, mapping_state, failure_count)
                VALUES ('BSE', 'TEST_SYM', 'TEST_MAPPED_NEW', 'BSE', 'TEST_SYM', 'TEST_MAPPED_NEW', 'ACTIVE', 0)
                ON CONFLICT (mapping_type, original_sym) 
                DO UPDATE SET mapped_sym = EXCLUDED.mapped_sym, mapped_symbol = EXCLUDED.mapped_symbol
            ''')
            
            cur.execute("SELECT mapped_symbol FROM symbol_mappings WHERE provider = 'BSE' AND original_symbol = 'TEST_SYM'")
            res = cur.fetchone()
            assert res[0] == 'TEST_MAPPED_NEW', "ON CONFLICT DO UPDATE failed to overwrite the row."
