# ELITE Breakout System - Invariant Registry

This document is automatically generated from the codebase. It lists all executable business invariants enforcing the pipeline's behavior.

| ID | Severity | Business Rule | Snapshot Stage | Owner | Tests |
|---|---|---|---|---|---|
| `INV-ALRT-001` | `CRITICAL` | Alert payload must contain originating candidate signals | `07_alert` | Architecture | `N/A` |
| `INV-DATA-001` | `WARNING` | Data quality must not fail due to missing/empty inputs | `01_validation` | Architecture | `N/A` |
| `INV-PIPE-001` | `CRITICAL` | Pipeline events must fire in canonical order | `N/A` | Architecture | `N/A` |
| `INV-PIPE-002` | `CRITICAL` | Every executed stage must produce a valid snapshot | `N/A` | Architecture | `N/A` |
| `INV-SCORE-001` | `CRITICAL` | Score must be at least 50.0 | `05_candidate` | Architecture | `N/A` |
| `INV-SL-001` | `CRITICAL` | Stop Loss must be strictly below Entry | `06_sl_target` | Architecture | `N/A` |
| `INV-TGT-001` | `CRITICAL` | Target 1 must be strictly above Entry | `06_sl_target` | Architecture | `N/A` |
