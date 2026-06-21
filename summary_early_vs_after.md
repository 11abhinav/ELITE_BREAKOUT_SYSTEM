# Elite Breakout System — Early vs After (Post-Fixes)

## Overview

This document compares the system state before the recent fixes ("Early") and after the applied changes ("After"). It highlights root causes, what was changed, and recommended next steps for verification and hardening.

---

## Early (pre-fix) — Key Problems

- Frequent runtime errors during startup ("current transaction is aborted") due to failed DDL/migrations running inside transactions.
- DB schema mismatches: alert_time types inconsistent across tables; UNIONs mixing TIMESTAMPTZ/TEXT.
- Views blocking ALTER TABLE operations (v_trade_analytics referenced columns being altered without DROP VIEW).
- Scheduler/timezone bugs: UTC vs IST inconsistencies caused alerts and trades outside market hours.
- performance_tracker errors: NameError (dt_time), TypeError (entry_date slicing), and missing performance_data.json.
- scanner_health CHECK violations: 'RUNNING' used while DB constraint only allowed ('OK','DOWN','IDLE').
- Reversal scanner re-alerting same symbols repeatedly (no cooldown/outcome awareness).
- Dashboard showed empty columns (CMP/ROE) and missing Wealth alerts.

---

## After (post-fix) — What Was Done

High-level goals: stop aborted transactions cascades, unify timestamps/timezones (IST), ensure safe idempotent migrations, and restore dashboard & tracker functionality.

Key fixes (file-by-file):

- app/database.py
  - Simplified and hardened migrations: DROP VIEW before ALTER, DROP DEFAULT before ALTER, run heavy DDL in autocommit to avoid leaving the transaction aborted.
  - Standardized alert_time to TIMESTAMPTZ (IST) and added safe conversions for existing text data.
  - Added or fixed CHECK constraints and indexes; ensured idempotent ALTERs (IF NOT EXISTS).
  - Implemented is_symbol_in_failed_reversal_cooldown(symbol, cooldown_days) with a numpy.busday_count path and a pure-Python fallback.
  - Added logging, connection pool timeouts, and safer get_connection() behavior.

- app/performance_tracker.py
  - Fixed NameError (dt_time -> use time/time import) and TypeError (entry_date may be date/datetime); added robust parsing.
  - Writes performance_data.json to data directory for the dashboard to consume.
  - Improved IST handling when fetching post-alert intraday bars and switched to safe intervals (5m/1h/1d) depending on age.

- app/reversal_scanner.py
  - Integrated cooldown helper; scoring reweighting and safety checks.
  - Added defensive DB calls and graceful fallbacks when outcome-tracking helper missing.

- app/main.py and scheduler logic
  - Rewrote scheduler to operate in IST and restrict execution to market hours (9:15–15:30 IST) and special full-run windows (Sunday / 1:30 AM refresh).

- Other small fixes
  - Replaced 'RUNNING' scanner status writes with allowed statuses (OK/IDLE) to avoid CheckViolation.
  - Fixed several indentation and commit-ready syntax issues.

---

## Migration & DB Safety Notes

- Migrations now run heavy DDL in autocommit mode to avoid "transaction aborted" cascades.
- Views that depend on columns are dropped (CASCADE) before column ALTERS and recreated after the migration.
- Conversions use safe-cast helper PL/pgSQL functions that return NULL on parse failure to avoid crashing the migration.
- Recommendation: take a DB snapshot/backup before running these migrations in production.

---

## New/Changed Behavior

- Timezone: All stored and canonical timestamps standardized to IST (ZoneInfo("Asia/Kolkata")).
- Scheduler: Market-hours-aware scheduler prevents trades outside NSE trading windows.
- Cooldown: Reversal scanner respects recent failed outcomes and suppresses re-alerts for configured business-day cooldown.
- Performance tracker now reliably builds performance_data.json consumed by the dashboard.

---

## Remaining Work & Recommendations

- Add unit tests for:
  - Migration idempotence and safe-cast behavior (run against a disposable Postgres instance).
  - is_symbol_in_failed_reversal_cooldown (numpy and fallback paths).
  - performance_tracker build flow (mock DB + data_fetcher).
- Centralize allowed status enums (alerts/scanner_health/telegram) into a constants module to avoid future CHECK violations.
- Add a lightweight CI job that runs python -m py_compile and a small subset of critical unit tests.
- Monitor logs after a deployment restart for the first 10 minutes to confirm no "current transaction is aborted" or CheckViolation errors.

---

## How to Verify / Commands

1. Ensure DATABASE_URL is set in the environment.
2. Restart service/container:

   docker-compose restart elite-breakout || docker restart <container>

3. Tail logs and check for errors (first 2–3 minutes):

   docker-compose logs -f elite-breakout | sed -n '1,200p'

4. Key things to confirm in logs:
   - No "current transaction is aborted" messages.
   - No psycopg2 CheckViolation for scanner_health.
   - Performance tracker writes performance_data.json.
   - Watchlist restored or rebuilt (if parquet missing).

---

## Notes

- The cooldown helper prefers numpy (numpy is optional). If numpy is absent, a pure-Python business-day fallback is used.
- Back up production DB before running migrations.
- If issues persist, capture the first 200 lines of logs and share them.

---

Created by: automation assistant
Date: 2026-06-21

