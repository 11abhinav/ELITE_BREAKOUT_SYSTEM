# PROSPECTIVE PAPER LEDGER SCHEMA
**Architecture:** Automated Append-Only Forward Holdout Ledger  
**Deployment Date:** 2026-09-28  

---

## 1. IMMUTABLE SCHEMA DEFINITION
```json
{
  "record_id": "UUID string",
  "strategy_version": "WEALTH_EXIT_V1 | WEALTH_EXIT_V2",
  "git_commit_sha": "string (40 hex chars)",
  "rules_hash": "string (SHA256)",
  "market_data_timestamp": "ISO8601 (IST)",
  "signal_timestamp": "ISO8601 (Session T Close)",
  "entry_timestamp": "ISO8601 (Session T+1 Open)",
  "symbol": "string (NSE symbol)",
  "entry_price": "float (Rupees)",
  "allocated_shares": "int",
  "portfolio_slot_index": "int (0-9)",
  "exit_timestamp": "ISO8601 | null",
  "exit_price": "float | null",
  "exit_reason": "string | null",
  "holding_days": "int | null",
  "realized_return_pct": "float | null",
  "mfe_pct": "float",
  "mae_pct": "float",
  "nifty_regime_at_entry": "BULL | SIDEWAYS | BEAR",
  "corporate_action_audit_status": "CLEAN | EXCLUDED",
  "record_hash": "string (SHA256 of entire record)"
}
```

---
*Authored by Elite Breakout System Research Engine.*
