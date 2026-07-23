# Elite Wealth System – Architecture Modernization Roadmap

**Version:** 1.0
**Status:** Approved Implementation Plan
**Purpose:** Provide a controlled, low-risk migration from the current stateless architecture to a stateful `SessionContext` architecture while preserving trading correctness.

---

# Guiding Principles

This modernization follows several non-negotiable engineering principles:

1. **Correctness before Performance**

   * Scanner outputs must remain identical during structural refactoring.
   * Performance improvements are secondary to correctness.

2. **Small, Reversible Changes**

   * Every stage should be independently deployable.
   * Every deployment should be easy to roll back.

3. **Observation Before Optimization**

   * Every structural change must be observed in production before additional changes are introduced.

4. **Single Ownership**

   * Every shared object has exactly one owner.
   * Other components receive read-only access.

5. **Evidence-Based Decisions**

   * Memory lifecycle, cache retention, and cleanup are based on verified code dependencies—not assumptions.

---

# Overall Roadmap

```
Phase 1
System Audit
        │
        ▼
Phase 2
SessionContext Foundation
        │
        ▼
Deploy
Observe
Validate
        │
        ▼
Phase 3
Shared Cache Migration
        │
        ▼
Deploy
Observe
Validate
        │
        ▼
Phase 4
Incremental Computation
        │
        ▼
Deploy
Observe
Validate
        │
        ▼
Phase 5
Event-Driven Cleanup
        │
        ▼
Deploy
Observe
Validate
        │
        ▼
Phase 6
Legacy Cleanup
```

---

# Phase 1 — Verified System Audit

## Objective

Understand the current system completely before making architectural changes.

## Deliverables

* Verified execution timeline
* Dataset inventory
* Cache inventory
* Scanner dependency graph
* Data lineage
* Memory ownership analysis
* Network audit
* Indicator audit
* Performance baseline

## Status

**Completed**

---

# Phase 2 — SessionContext Foundation

## Objective

Introduce the architectural skeleton without changing scanner behaviour.

## Scope

Implement:

* SessionContext
* Manager interfaces
* CachePolicy
* Session state machine
* Service interfaces
* Architectural tests

## Explicitly Out of Scope

* No cache migration
* No incremental computation
* No memory optimization
* No scanner logic changes
* No cleanup changes

## Expected Behaviour

The application should behave exactly as before.

Scanner outputs must remain identical.

## Deployment Strategy

Deploy immediately after completion.

Observe for 1–2 trading days.

## Success Criteria

* Zero output drift
* No new exceptions
* Stable memory
* Stable scheduler
* State transitions working
* Existing telemetry unaffected

---

# Phase 3 — Shared Cache Migration

## Objective

Move ownership of shared datasets into SessionContext.

## Migration Order

### Stage 3.1

Watchlist

Deploy

Observe

### Stage 3.2

Fundamentals

Deploy

Observe

### Stage 3.3

Daily OHLCV

Deploy

Observe

### Stage 3.4

Intraday OHLCV

Deploy

Observe

### Stage 3.5

Remaining caches

* Market Regime
* Hidden caches
* Indicator bundles
* Supporting metadata

Deploy

Observe

## Rules

Never migrate multiple critical datasets together.

Every migration must preserve scanner outputs.

---

# Phase 4 — Incremental Computation

## Objective

Replace unnecessary full recomputation with mathematically correct incremental updates.

## Candidates

Incremental:

* EMA
* ATR
* RSI
* MACD (if mathematically valid)

Dirty Region:

* Pivot Detection
* Swing Detection
* Support/Resistance

Remain Full Rebuild:

* Cross-sectional rankings
* Momentum Z-Scores
* Percentiles
* Relative Strength rankings

## Validation

For every scanner:

Incremental Output == Full Rebuild Output

Must hold for all test datasets.

---

# Phase 5 — Event-Driven Cleanup

## Objective

Replace manual cleanup with dependency-driven lifecycle management.

## Principles

Caches are released when:

* Last consumer finishes
* Session ends
* Cache policy expires

Never release based solely on arbitrary clock time.

## Cleanup Examples

* Intraday OHLCV → released after final intraday consumer
* Daily OHLCV → released after Pullback (or whichever scanner is verified as the final consumer)
* Temporary scoring DataFrames → released immediately after scanner completion

---

# Phase 6 — Legacy Cleanup

## Objective

Remove obsolete infrastructure after the new architecture has proven stable.

## Remove

* Legacy global caches
* Duplicate ownership
* Manual cleanup logic
* Deprecated helper functions
* Old cache implementations

Only remove code after verifying that the replacement has been stable in production.

---

# Deployment Policy

Every stage follows the same lifecycle:

```
Implement
        │
        ▼
Local Testing
        │
        ▼
Regression Testing
        │
        ▼
Deploy
        │
        ▼
Production Observation
        │
        ▼
Compare Outputs
        │
        ▼
Approve Next Stage
```

No stage proceeds until the previous stage is verified.

---

# Observation Checklist

After every deployment monitor:

## Functional

* Scanner outputs
* Candidate lists
* Alerts
* Database writes
* Scheduler execution

## Performance

* RSS memory
* CPU utilisation
* Runtime per scanner
* Network requests
* Cache hits/misses

## Stability

* Exceptions
* Timeouts
* Deadlocks
* Memory growth
* State transitions

---

# Rollback Policy

Rollback immediately if any of the following occur:

* Scanner output changes unexpectedly
* Missing or duplicate alerts
* Increased failure rate
* Memory regression
* Scheduler instability
* Data corruption
* Significant runtime degradation

Every deployment should be independently reversible.

---

# Final Success Criteria

The architecture modernization is complete only when all of the following are true:

* Scanner outputs remain identical to the legacy implementation.
* Shared datasets have a single owner.
* Duplicate downloads are eliminated where intended.
* Incremental calculations are mathematically validated.
* Event-driven cache lifecycle is operational.
* Memory remains stable over a full trading session.
* Midnight session reset leaves no stale state.
* Legacy cache implementations have been removed.
* Telemetry continues to provide complete observability.

---

# Long-Term Engineering Principles

* Prefer correctness over optimization.
* Prefer evidence over assumptions.
* Prefer incremental delivery over large rewrites.
* Prefer explicit ownership over shared mutable state.
* Keep architecture observable through permanent telemetry.
* Deploy small, observe carefully, then continue.
