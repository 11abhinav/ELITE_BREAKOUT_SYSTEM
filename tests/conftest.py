"""
Session-wide test safety configuration.

[RULE 67 CHANGE-RATIONALE: P0 TEST->PRODUCTION DB ISOLATION]
The quarantine store (app.pit_recovery_cache.PitRecoveryStatusStore) syncs to the
production PostgreSQL `pit_recovery_status` table. Tests must never mutate (or
read-seed from) that table. This disables quarantine DB sync for the entire
pytest session before any app module is imported.
"""
import os

os.environ["QUARANTINE_DB_SYNC_ENABLED"] = "false"
