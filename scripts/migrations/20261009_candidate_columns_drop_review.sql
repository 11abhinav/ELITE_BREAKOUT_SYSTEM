-- ==============================================================================
-- FORENSIC MIGRATION REVIEW SPECIFICATION (REVIEW ONLY — DO NOT EXECUTE)
-- Migration ID: 20261009_candidate_columns_drop_review
-- Target Tables: public.alerts (Primary), public.alert_outcomes (Secondary)
-- Status: EXECUTION STRICTLY PROHIBITED / ON HOLD
-- ==============================================================================
--
-- [MANDATORY GOVERNANCE WARNING]
-- Re-creating dropped columns via `ALTER TABLE alerts ADD COLUMN IF NOT EXISTS ...`
-- DOES NOT RESTORE LOST HISTORICAL DATA, DEFAULT VALUES, OR DEPENDENT INDEXES.
-- Any destructive drop irreversibly destroys trade audit history stored in those columns.
-- The ONLY valid disaster-recovery procedure is full restoration from a verified pg_dump:
--   pg_dump -Fc -v -h <host> -U <user> -d <db> -t alerts -t alert_outcomes > pre_drop_backup.dump
--
-- ==============================================================================
-- SECTION 1: PER-TABLE OWNERSHIP & COUPLING AUDIT
-- ==============================================================================
--
-- TABLE 1: alerts
-- ------------------------------------------------------------------------------
-- Active Application Code Dependencies (MUST NOT DROP):
--   - target_4             : Selected in app/database.py (L5504, L5546, L5594, L5635, L9428)
--                          : Selected in app/master_orchestrator.py (L993)
--                          : Inserted in app/database.py (L3373, L3384)
--   - sl_method            : Physical column read in app/master_orchestrator.py (L994, L982)
--   - target_method        : Physical column read in app/master_orchestrator.py (L994, L983)
--   - cash_in_hand         : Inserted in app/database.py (L3375, L3386)
--   - rvol_rolling         : Inserted in app/database.py (L3379, L3389)
--   - reference_entry_open : Inserted in app/database.py (L12424, L12462)
--   - next_trading_day     : Inserted in app/database.py (L12424, L12461)
--   - last_event_id        : Updated in app/database.py (L3259)
--   - shadow_status        : Updated/Queried in app/database.py (L3911, L4170, L4389)
--   - shadow_exit_price    : Updated/Queried in app/database.py (L3911, L4170, L4389)
--   - shadow_pnl_pct       : Updated/Queried in app/database.py (L3911, L4170, L4389)
--   - shadow_closed_at     : Updated/Queried in app/database.py (L3912, L4170, L4389)
--
-- Potentially Unused in alerts (Candidate for Verification Only):
--   - partial_exit_pct     : Defined in alerts DDL (L564). 0 application references.
--   - realized_r           : Defined in alerts DDL (L559). Replaced by net_realized_r. 0 direct references.
--   - date_status          : Defined in alerts DDL (L569). NOT inserted into alerts (only alert_outcomes).
--
-- ------------------------------------------------------------------------------
-- TABLE 2: alert_outcomes
-- ------------------------------------------------------------------------------
-- Active Application Code Dependencies (DO NOT CONFUSE WITH alerts TABLE):
--   - date_status          : Inserted into alert_outcomes in app/database.py (L3449, L3457)
--   - target_4             : Inserted into alert_outcomes in app/database.py (L3448, L3455)
--
-- ==============================================================================
-- SECTION 2: PRODUCTION VERIFICATION QUERIES (NON-DESTRUCTIVE)
-- ==============================================================================
-- Run these read-only queries against production to inspect real data population
-- before any code decoupling is even scheduled:
--
-- 1. Check alerts candidate column row counts and non-null rates:
--    SELECT
--        count(*) AS total_rows,
--        count(target_4) AS non_null_target_4,
--        count(sl_method) AS non_null_sl_method,
--        count(target_method) AS non_null_target_method,
--        count(cash_in_hand) AS non_null_cash_in_hand,
--        count(rvol_rolling) AS non_null_rvol_rolling,
--        count(reference_entry_open) AS non_null_reference_entry_open,
--        count(next_trading_day) AS non_null_next_trading_day,
--        count(last_event_id) AS non_null_last_event_id,
--        count(shadow_status) AS non_null_shadow_status,
--        count(partial_exit_pct) AS non_null_partial_exit_pct,
--        count(realized_r) AS non_null_realized_r,
--        count(date_status) AS non_null_date_status
--    FROM alerts;
--
-- 2. Check alert_outcomes table for separate date_status and target_4 dependencies:
--    SELECT
--        count(*) AS total_outcome_rows,
--        count(date_status) AS non_null_date_status_in_outcomes,
--        count(target_4) AS non_null_target_4_in_outcomes
--    FROM alert_outcomes;
--
-- ==============================================================================
-- SECTION 3: CONDITIONAL REVIEW SPECIFICATION (HELD — FOR CODE-DECOUPLED FUTURE)
-- ==============================================================================
-- WARNING: This block is strictly a draft for when application code decoupling
-- in app/database.py and app/master_orchestrator.py is complete, merged, and certified.

/*
BEGIN;

-- Safety Gate: Assert zero active views depend on candidate columns
DO $$
DECLARE
    view_dep_count INTEGER;
BEGIN
    SELECT count(*) INTO view_dep_count
    FROM information_schema.view_column_usage
    WHERE table_name = 'alerts'
      AND column_name IN ('partial_exit_pct', 'realized_r', 'date_status');

    IF view_dep_count > 0 THEN
        RAISE EXCEPTION 'MIGRATION ABORTED: Views are actively bound to candidate columns!';
    END IF;
END $$;

-- Drop only decoupled, verified, zero-data columns (Example: Phase 1 candidates)
-- ALTER TABLE alerts DROP COLUMN IF EXISTS partial_exit_pct;
-- ALTER TABLE alerts DROP COLUMN IF EXISTS realized_r;
-- ALTER TABLE alerts DROP COLUMN IF EXISTS date_status;

COMMIT;
*/

-- ==============================================================================
-- SECTION 4: AUTHORITATIVE RECOVERY PROCEDURE
-- ==============================================================================
-- DO NOT RELY ON `ALTER TABLE ADD COLUMN` AS ROLLBACK.
-- If an unauthorized drop occurs, execute table-level restoration immediately:
--
-- Step 1: Terminate active connections to avoid lock contention
-- SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'elite_breakout' AND pid <> pg_backend_pid();
--
-- Step 2: Restore from verified pre-migration backup
-- pg_restore -v --clean --if-exists -h <host> -U <user> -d <db> -t alerts pre_drop_backup.dump
--
-- Step 3: Validate row count and column integrity
-- python3 scripts/migration_validator.py
-- ==============================================================================
