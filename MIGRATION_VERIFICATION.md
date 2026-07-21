# Migration Verification Report

This report validates that every single table in the Old Database has been perfectly cloned into the New Database, row-by-row.

| Table Name | Old DB Row Count | New DB Row Count | Match? |
|------------|------------------|------------------|--------|
| ai_concall_cache | 8 | 8 | ✅ YES |
| ai_concall_cache_v2 | 4 | 4 | ✅ YES |
| ai_concall_cache_v3 | 1012 | 1012 | ✅ YES |
| alerts | 110 | 110 | ✅ YES |
| bayesian_model_updates | 0 | 0 | ✅ YES |
| breakout_watchlist | 341 | 340 | ❌ NO |
| build_manifest | 18 | 18 | ✅ YES |
| candidates | 0 | 0 | ✅ YES |
| capital_history | 79 | 79 | ✅ YES |
| daily_excluded_watchlist | 682 | 682 | ✅ YES |
| daily_send_log | 7 | 7 | ✅ YES |
| daily_watchlist | 304 | 304 | ✅ YES |
| data_cache_metadata | 345 | 345 | ✅ YES |
| data_fetch_health | 14 | 14 | ✅ YES |
| fetch_errors | 3524 | 3523 | ❌ NO |
| global_notifications | 0 | 0 | ✅ YES |
| manual_portfolio | 0 | 0 | ✅ YES |
| parquet_cache | 446 | 446 | ✅ YES |
| promoter_pledge_cache | 1211 | 1211 | ✅ YES |
| push_subscriptions | 2 | 2 | ✅ YES |
| rejected_alerts | 5 | 5 | ✅ YES |
| scan_failures | 0 | 0 | ✅ YES |
| scanner_health | 34 | 34 | ✅ YES |
| scanner_runs | 4 | 4 | ✅ YES |
| score_weight_log | 4 | 4 | ✅ YES |
| symbol_mappings | 51 | 51 | ✅ YES |
| system_checkpoints | 0 | 0 | ✅ YES |
| system_logs | 77758 | 77758 | ✅ YES |
| system_state | 7 | 7 | ✅ YES |
| telegram_queue | 414 | 414 | ✅ YES |
| trade_audit_log | 161 | 161 | ✅ YES |
| user_messages | 1 | 1 | ✅ YES |
| user_sessions | 444 | 444 | ✅ YES |
| users | 5 | 5 | ✅ YES |
| validation_history | 507 | 494 | ❌ NO |
| watchlist | 289 | 289 | ✅ YES |
| wealth_buy_alert | 31 | 31 | ✅ YES |
| wealth_score_history | 833 | 833 | ✅ YES |

## Conclusion
**WARNING:** There is a discrepancy in row counts!
