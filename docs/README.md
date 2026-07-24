# ELITE BREAKOUT SYSTEM — DOCUMENTATION INDEX

> **Notice**: These specifications reflect the active source code implementation under `app/`. The source implementation remains the ultimate source of truth.

---

## 📚 Canonical Specifications

- **[ENGINEERING SPECIFICATION](ENGINEERING_SPECIFICATION.md)** (`ENGINEERING_SPECIFICATION.md`) — Answers *"What exists and how does it work?"* High-level architectural specification covering repository partitioning, startup execution lifecycle, wall-clock scheduler triggers, scanner engine flows, component catalog, state transitions, PostgreSQL connection pooling, memory ownership, `InstrumentedLock` contention metrics, and `TradeStructureValidator` invariants.
- **[BUSINESS LOGIC SPECIFICATION](BUSINESS_LOGIC_SPECIFICATION.md)** (`BUSINESS_LOGIC_SPECIFICATION.md`) — Answers *"Exactly how is it implemented?"* Complete business logic contract detailing core scanner specifications, Multi-TF 15-minute candle-aligned schedule, EOD/Reversal post-Bhavcopy `force=True` execution, scoring mechanics, Stop-Loss Anti-Trap algorithms, and risk invariants.
- **[SYSTEM GUIDE](SYSTEM_GUIDE.md)** (`SYSTEM_GUIDE.md`) — Operational runbook detailing system lifecycle, 24/7 IST schedule, failure modes, provider fallback chains, performance baselines, SLAs, and manual trigger runbooks.
- **[CHANGELOG](CHANGELOG.md)** (`CHANGELOG.md`) — Complete version history and deprecation records across system releases.

---

## 📜 Engineering Policies & Governance

- **[PERFORMANCE VALIDATION GUIDE](PERFORMANCE_VALIDATION_GUIDE.md)** (`PERFORMANCE_VALIDATION_GUIDE.md`) — Governance rules, optimization approval workflows, and golden snapshot testing requirements.

---

## 🔒 Verification Basis
- **Git Commit Hash**: `e30de7a3f898380cf0d7f99991448b4883e44fa1`
- **Generation Date**: `2026-07-24`
- **Repository Branch**: `main`
