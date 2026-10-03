# QUALITY_VALUE_RECOVERY_WEALTH_V1 — 10-SESSION PAPER VALIDATION PLAN

**Strategy ID:** `QUALITY_VALUE_RECOVERY_WEALTH_V1`  
**Governance Protocol:** Mandatory 10-Session Forward Paper Tracking Protocol  
**Start Date:** 2026-10-04  
**Target Duration:** 10 consecutive active NSE trading sessions  
**Broker Execution:** `DISABLED` (Paper tracking & audit logging only)  

---

## 1. OBJECTIVE & INVARIANTS

The 10-Session Paper Validation Phase evaluates live market performance, data pipeline stability, signal generation, and exit tracking under actual live market conditions without placing live broker orders.

### Core Rules
1. **Absolute Strategy Freeze:** Zero changes permitted to entry parameters (ROCE $\ge 15\%$, Sales CAGR $\ge 10\%$, PAT CAGR $\ge 10\%$, CFO/PAT $\ge 0.8$, D/E $\le 0.5$, EV/EBITDA discount $\ge 25\%$, Drawdown $\ge 30\%$) or Model E3 exit logic during the 10-session period.
2. **Separation of Forward Paper History:** Paper forward tracking records are maintained separately from historical research/backtest datasets in `reports/certification/TEN_SESSION_PAPER_VALIDATION_REPORT.json`.
3. **Execution Model:** Signal generated at Session $T$ Close ($17:15$ IST daily scan). Reference entry recorded at Session $T+1$ Open price.

---

## 2. DAILY TRACKING MATRIX

For each candidate qualifying during the 10 trading sessions, record:
- `signal_date` (Session $T$)
- `symbol`
- `reference_entry_open` (Session $T+1$ Open price)
- `current_price` (CMP)
- `mfe` (Maximum Favorable Excursion)
- `mae` (Maximum Adverse Excursion)
- `holding_duration_days`
- `e3_exit_status` (`HOLD` or structural exit reason)
- `realized_r_return`

---

## 3. SESSION EXECUTION PROTOCOL

```text
Session T (17:15 IST)
  ↓
Daily Scheduled Scan Runs via Main Orchestrator
  ↓
Data gate checks canonical PIT dataset & Upstox quotes
  ↓
Candidate qualification recorded in DB alerts table
  ↓
Session T+1 (09:15 IST)
  ↓
T+1 Open price captured as reference entry
  ↓
Session T+1 (18:30 IST)
  ↓
E3 exit monitor pulse checks active candidates
```

---

## 4. FINAL PAPER ACCEPTANCE CRITERIA

At the conclusion of Session 10:
- Zero data pipeline crashes or silent fallbacks.
- Zero global scanner lock deadlocks or execution skews.
- 100% of candidate alerts tracked with valid $T+1$ reference entry prices.
- All exit conditions evaluated accurately by Model E3.

```text
PAPER_VALIDATION_STATUS = READY_FOR_SESSION_1
```
