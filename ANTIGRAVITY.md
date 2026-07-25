# ANTIGRAVITY — ELITE BREAKOUT SYSTEM ARCHITECTURAL RULES & DIRECTORY MAP

> **Document Class:** Root Architectural Summary & System Map
> **Target File:** `ANTIGRAVITY.md`
> **Last Synchronized:** 2026-07-25 (v8.4.3+)

---

## 1. CANONICAL DOCUMENTATION SUITE (`docs/`)

All architectural decisions, system specifications, formulas, and reconstruction blueprints MUST be synchronized across the 6 canonical documentation files:

1. **User & Operations Manual**: [SYSTEM_ARCHITECTURE_GUIDE.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/docs/SYSTEM_ARCHITECTURE_GUIDE.md)
   - High-level architecture, scanner capabilities, market schedules, active ADRs (ADR-001 through ADR-012), and documentation coverage report.
2. **Quantitative Blueprint & System Specification**: [SYSTEM_SPECIFICATION.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/docs/SYSTEM_SPECIFICATION.md)
   - Mathematical formulas (RSI, ADX, EMA, ATR, `FM_Score`, `ScoringEngine`), filter cascades for all 6 scanners, stop-loss/target calculations, PostgreSQL DDLs, and REST API schemas.
3. **Step-by-Step System Reconstruction Spec**: [SYSTEM_RECONSTRUCTION_SPEC.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/docs/SYSTEM_RECONSTRUCTION_SPEC.md)
   - Module directory map, Python dependencies, environment variable specs, database bootstrap scripts, and verification test suites.
4. **V9 Clean Architecture Specification & Deprecation Protocol**: [V9_ARCHITECTURE_SPECIFICATION.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/docs/V9_ARCHITECTURE_SPECIFICATION.md)
   - Clean 5-layer target architecture (`src/`), `PipelineStep` abstractions, `PipelineContext`, status matrix, and deprecation log.
5. **Upcoming Improvements & Roadmap**: [UPCOMING_IMPROVEMENTS.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/docs/UPCOMING_IMPROVEMENTS.md)
   - Completed milestones, future V9 refactoring items, and deprecation tracking.
6. **System Changelog**: [CHANGELOG.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/docs/CHANGELOG.md)
   - Chronological changelog tracking all architectural releases up to `v8.4.3`.

---

## 2. CORE SYSTEM INVARIANTS

1. **IST Timezone Rule**: All timing, trading schedules, candle calculations, and database timestamps MUST be evaluated in **IST (UTC+5:30)**.
2. **RS Currency Rule**: All financial amounts, target gains, and values MUST be denominated in **Indian Rupees (₹ / RS)**.
3. **Documentation Deprecation Protocol (Rule 58)**: Replaced or deprecated rules/features MUST NOT be silently deleted—they MUST use Markdown strike-through formatting (`~~old feature/rule~~`) accompanied by an explicit deprecation annotation (e.g., `*(Replaced on YYYY-MM-DD by <new_feature>)*`).
4. **Per-Symbol Granular RAM Cache (ADR-003)**: `_cache[(interval, period)][symbol] = {"data": df, "ts": monotonic_ts}` with per-symbol TTL tracking.
5. **Full-Universe Candidate Accumulation (ADR-005)**: Scanners accumulate candidate setups across all universe chunks before executing global score sorting and `SCANNER_MAX_ALERTS` (top 10) truncation.
6. **Data Provider Selector Boundary (ADR-006)**: Data acquisition routing is strictly delegated to `ProviderSelector` in `app/data_providers/provider_selector.py`.

---
*End of Root System Specification — `ANTIGRAVITY.md`*
