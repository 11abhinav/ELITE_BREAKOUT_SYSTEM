# QUALITY_VALUE_RECOVERY — Exit Monitor Routing & Governance Audit Report

**Strategy ID:** `QUALITY_VALUE_RECOVERY`  
**Exit Evaluator Class:** `CanonicalRecoveryE3ExitEvaluator`  
**Exit Logic Model:** Model E3 (Trailing 3-Quarter PAT Deceleration + EV/EBITDA Valuation Mean-Reversion Re-Rating + 10-Session Max Window)  
**Pulse Schedule:** 15:15 IST (Pre-Close Warning Pulse) & 18:30 IST (Definitive EOD Exit Pulse)  
**Date:** 2026-10-03  
**Status:** `EXIT_MONITOR_INTEGRATED`

---

## 1. Exit Evaluation Routing Architecture

```text
app/live_wealth_monitor.py
  └── run_v2_exit_check()
        ├── DB Query: SELECT * FROM alerts WHERE scanner IN ('QUALITY_COMPOUNDER', 'QUALITY_COMPOUNDER_VALUE_V2_FINAL', 'QUALITY_VALUE_RECOVERY', 'QUALITY_VALUE_RECOVERY_WEALTH_V1') AND status IN ('OPEN', 'ACTIVE')
        └── Routing Rule:
              if scanner in ('QUALITY_VALUE_RECOVERY', 'QUALITY_VALUE_RECOVERY_WEALTH_V1'):
                  evaluator = CanonicalRecoveryE3ExitEvaluator()
                  verdict = evaluator.evaluate_exit(alert, current_market_data)
```

---

## 2. Model E3 Fundamental Exit Logic

| Gate / Rule | Condition | Action |
| :--- | :--- | :--- |
| **PAT Deceleration Gate** | Trailing 3-Quarter PAT growth $< 0\%$ | Trigger Fundamental Exit Signal (`REASON = PAT_DECELERATION`) |
| **Valuation Re-Rating Gate** | Current EV/EBITDA $\ge$ 3Y Median EV/EBITDA | Trigger Fundamental Exit Signal (`REASON = VALUATION_RE_RATED`) |
| **Time Holding Limit** | Holding period $\ge 10$ trading sessions | Trigger Time-Based Exit Signal (`REASON = MAX_HOLDING_EXPIRED`) |
| **Structural Protection** | Stop loss hit ($10\%$) or ATR trailing stop hit | Trigger Hard Exit Signal (`REASON = STOP_LOSS_HIT`) |

---

## 3. Database State Progression Audit

```text
ALERT CREATED (17:15 IST Scan)
  │
  ▼
alerts table status = 'OPEN'
  │
  ▼ (Position Taken / Confirmed)
alerts table status = 'ACTIVE'
  │
  ▼ (Model E3 Exit Rule Triggered at 15:15 or 18:30 IST)
alerts table status = 'EXIT_SIGNAL'
  │
  ▼ (Exit Execution Confirmed)
alerts table status = 'CLOSED'
```

---

## 4. Exit Governance Invariants

- [x] **Isolation:** Recovery strategy exits are completely decoupled from momentum/breakout SMA exits.
- [x] **Dual Daily Pulses:** Evaluated at 15:15 IST (enables execution before market close) and 18:30 IST (EOD reconciliation).
- [x] **Audit Trail:** Every exit signal records exact metric values, decision timestamps, and exit reasons in `scanner_execution_history`.
