# ANTIGRAVITY — ELITE BREAKOUT SYSTEM ARCHITECTURAL MANDATE & DIRECTORY MAP

> **Document Class:** Root Architectural Summary & System Map
> **Target File:** `ANTIGRAVITY.md`
> **Last Synchronized:** 2026-07-25 (v8.4.3+)

---

## 1. STRICT TWO-DOCUMENT DOCUMENTATION MANDATE

Per system mandate, the repository documentation under `docs/` is consolidated into **EXACTLY TWO MASTER CANONICAL DOCUMENTS**:

1. **User & Admin Functional Specification**: [SYSTEM_SPECIFICATION.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/docs/SYSTEM_SPECIFICATION.md)
   - **Target Audience**: Users, Traders, Portfolio Managers, System Administrators.
   - **Scope**: Explains **WHAT** the system does and **HOW TO USE IT**.
   - **Contents**: Full functional specifications for all 6 scanning engines (EOD Breakout, Multi-TF Intraday, Reversal, Pullback Pipeline, Wealth Engine, Multibagger Engine), trade signal entry/exit rules, initial & trailing stop-loss mechanics, multi-target profit taking (T1..T4), exit alerting, operational user workflows for all 3 dashboards (User Dashboard, Admin Dashboard, Performance Tracker Dashboard), notification channels (Telegram, Web Push, Portal), and 24-hour system operating timelines.

2. **Developer & AI Technical Architecture & Reconstruction Blueprint**: [SYSTEM_ARCHITECTURE.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/docs/SYSTEM_ARCHITECTURE.md)
   - **Target Audience**: Core Systems Engineers, Software Developers, AI Coding Models.
   - **Scope**: Explains **HOW THE SYSTEM IS DESIGNED & WORKS INTERNALLY**.
   - **Contents**: Complete technical blueprint containing all mathematical & quantitative algorithms (RSI, ADX, EMA, ATR, `FM_Score`, `ScoringEngine` 0-100 breakdown, `compute_sl_and_target`, `TradeStructureValidator`, regime policies, forensic risk tiers), step-by-step scanner code flows, full-universe candidate accumulation and top-10 truncation, data acquisition routing (`ProviderSelector`), data provider fallback chains (Fyers API -> YFinance -> BSE), 3-tier per-symbol `_cache` RAM topology, timestamp normalization, BSE mapping state machine, Bhavcopy 0-to-4 day lookback fallback, complete PostgreSQL DDLs (all 42 tables, indexes, constraints, pool settings), mutex lock hierarchies (`InstrumentedLock`, `ProcessLock` advisory locks), REST API specs with payload JSON schemas, 88+ module directory map, 17 Production Deployment Gates, and V9 clean architecture blueprint with deprecation log (`~~old rule~~` + annotations).

---

## 2. CORE SYSTEM INVARIANTS

1. **IST Timezone Rule**: All timing, trading schedules, candle calculations, and database timestamps MUST be evaluated in **IST (UTC+5:30)**.
2. **RS Currency Rule**: All financial amounts, target gains, and values MUST be denominated in **Indian Rupees (₹ / RS)**.
3. **Strict 2-Document Rule (Rule 58)**: No other documentation files may exist under `docs/`. All operational details live in `docs/SYSTEM_SPECIFICATION.md` and all technical/architectural reconstruction details live in `docs/SYSTEM_ARCHITECTURE.md`.
4. **Per-Symbol Granular RAM Cache (ADR-003)**: `_cache[(interval, period)][symbol] = {"data": df, "ts": monotonic_ts}` with per-symbol TTL tracking.
5. **Full-Universe Candidate Accumulation (ADR-005)**: Scanners accumulate candidate setups across all universe chunks before executing global score sorting and `SCANNER_MAX_ALERTS` (top 10) truncation.
6. **Data Provider Selector Boundary (ADR-006)**: Data acquisition routing is strictly delegated to `ProviderSelector` in `app/data_providers/provider_selector.py`.
7. **Un-nested Candidate Verification Locks**: Summary reporting, DB alert verification, `upsert_scanner_health()`, and memory purges run un-nested at function scope.

---
*End of Root System Specification — `ANTIGRAVITY.md`*
