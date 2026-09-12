"""
Verify V5.29 Shadow Telemetry Tables in data/shadow_telemetry.db
"""
import sqlite3
from engine.production.v529_shadow_execution_engine import V529ShadowExecutionEngine

engine = V529ShadowExecutionEngine()

with sqlite3.connect("data/shadow_telemetry.db") as conn:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print("CURRENT SHADOW DB TABLES:", tables)

assert "v529_shadow_alert_telemetry" in tables
assert "v529_shadow_trigger_telemetry" in tables
print("V5.29 SHADOW TELEMETRY TABLES INITIALIZED AND VERIFIED.")
