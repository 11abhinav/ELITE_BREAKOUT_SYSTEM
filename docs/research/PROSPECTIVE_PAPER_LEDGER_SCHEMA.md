# PROSPECTIVE PAPER LEDGER SCHEMA
**Architecture:** Automated Append-Only Forward Holdout Ledger  
**Deployment Date:** 2026-09-28  
**Governance Invariant:** Mandatory Pre-Registered Cluster-Aware Holdout Gate  

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
  
  "execution_metrics": {
    "signal_close_T": "float (Official Session T Close)",
    "expected_T1_open": "float (Pre-Market Expected Open, Pegged to T Close)",
    "observed_T1_open": "float (First Observable Exchange Open Tick at 09:15:00 IST)",
    "actual_executable_price": "float (Achieved Fill Price)",
    "slippage": "float (actual_executable_price - expected_T1_open)",
    "slippage_pct": "float (Percentage Slippage)",
    "overnight_gap_pct": "float ((observed_T1_open - signal_close_T) / signal_close_T * 100)"
  },
  
  "allocated_shares": "int",
  "portfolio_slot_index": "int (0-9 | null for unlimited shadow)",
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

## 2. PRE-REGISTERED STATISTICAL GATE (CLUSTER-AWARE)

The holdout evaluation is governed by a **compound 4-way requirement** to prevent false discovery from clustered market events:

```text
Holdout remains INCONCLUSIVE until ALL 4 conditions pass:
  1. RAW_TRADE_COUNT >= 196
  2. EFFECTIVE_INDEPENDENT_SAMPLE_SIZE (N_eff) >= 196 (Kish's Deff = 1 + (m_bar - 1)*ICC)
  3. CALENDAR_DURATION >= 6 calendar months
  4. OBSERVED_REGIMES >= 2 distinct market regimes

Automatic live broker orders remain strictly BLOCKED until all 4 criteria are satisfied.
```

---
*Authored by Elite Breakout System Research Engine.*
