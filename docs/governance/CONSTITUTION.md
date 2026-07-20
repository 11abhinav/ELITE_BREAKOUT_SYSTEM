# Production Governance Framework ("The Constitution")
**Version:** 1.0 | **Status:** Frozen | **Immutability:** This document cannot be edited in place. Any future changes require a formal Level 4 Change Request and a bump to v1.1.

> **THE GUIDING PHILOSOPHY:**
> The business specification is authoritative. The implementation must conform to the specification. Golden datasets and snapshots are approved verification artifacts that demonstrate conformance to the specification; they do not define intended behavior. Any intentional change to the specification requires explicit approval before implementation.

---

## Non-Negotiable Principles
The following principles shall never be violated unless this Constitution itself is amended through a Level 4 Change Request.
1. Correctness is always preferred over convenience.
2. Silent behavior changes are prohibited.
3. Silent data loss is prohibited.
4. Silent validation bypasses are prohibited.
5. Invalid data must never replace higher-quality validated data.
6. Every business decision must be traceable to a Rule ID.
7. Every production alert must be explainable from validated inputs.
8. Every failure must be observable.
9. AI may optimize implementation but may not redefine business intent.
10. The Constitution governs all other project artifacts.

---

## Order of Authority
If governance artifacts conflict, the following strict hierarchy of authority shall prevail:
1. The Constitution (This document)
2. Architecture Freeze
3. Contracts Freeze
4. Business Rules Freeze
5. Dependency Freeze
6. Golden Datasets
7. Invariant Suite
8. Source Code
9. Unit Tests
10. Generated Documentation

---

## The Immutable Pillars

### Pillar 1: Architecture Freeze (`ARCHITECTURE_FREEZE.md`)
The highest-level system design contract.
*   **Provider Priority:** Fyers → Yahoo Finance → BSE Fallback
*   **Cache Lifecycle:** Expiry rules for Fundamentals (15 days), Price (Daily/Intraday).
*   **State Transitions:** Strict progression of `SETUP_ARMED` → `ENTRY_READY` → `TRADE_ACTIVE`.
*   **Database Engine:** PostgreSQL exclusively for persistent state.

### Pillar 2: Contracts Freeze (`CONTRACTS_FREEZE.md`)
Freezes the interfaces and schemas of the system.
*   **Public APIs & Endpoints**
*   **Core Function Signatures** (e.g., `compute_sl_and_target(inputs) -> 13 fields`)
*   **DTO / Dataclass Schemas** & **Database Schema**
*   **JSON Formats & Validation Report Schemas**

### Pillar 3: Business Rules Freeze (`BUSINESS_RULES.md`)
Every decision the scanner makes is a cataloged rule.
*   **Separation of Specification vs. Implementation:** The Rule is the authoritative specification. Traceability flows strictly as: `Specification (Rule)` → `Implementation (Scanner code)` → `Verification (Unit Tests, Golden Dataset, Snapshots, Invariants)`.
*   **Rule Lifecycle States:** `Draft` → `Proposed` → `Approved` → `Frozen` → `Deprecated` → `Retired`.

### Pillar 4: External Dependencies Freeze (`DEPENDENCY_FREEZE.md`)
Track approved versions and compatibility ranges.
*   **Libraries:** `Python`, `pandas`, `yfinance`, `Fyers SDK`.
*   **Provider Contracts:** Expected symbol formats or API response payloads.

### Pillar 5: Behavior Freeze (Snapshots & Golden Datasets)
Elevating snapshot testing into a rigid Golden Pipeline with protected baselines.
*   **Verification Mechanisms:** Every Rule ID shall be verified by one or more approved verification artifacts (unit tests, invariant tests, golden datasets, or behavioral snapshots), with at least one appropriate verification mechanism required.
*   **Data Lineage Tracking:** Every pipeline object must carry metadata (`provider`, `fetch_time`, `validation_status`, `cache_source`, `hash`) to trace bad alerts directly to their source.

### Pillar 6: Invariant Suite
Timeless truths that must always pass, regardless of implementation or snapshots.
*   **Mathematical:** `Score ∈ [0,100]`, `StopLoss < Entry`, `Candidate count >= Alert count`.
*   **Operational:** Provider failures never crash a scanner; Invalid data never overwrites higher-quality cache; Dead symbols never become active without cache expiry.
*   **Observability:** Every scanner rejection has a machine-readable reason code; Every provider failure has a standardized classification; Every state transition is logged; Every validation rejection produces a report.
*   **Data Integrity:** Every externally sourced datum must pass through validation before it is consumed by business logic.

### Pillar 7: AI Governance Policy (`AI_CHANGE_POLICY.md`)
A strict code of conduct injected into the workspace rules.

> [!CAUTION]
> **VERBATIM RULE:** 
> An AI must never modify any governed artifact—including architecture documents, business rules, contracts, golden datasets, snapshots, invariants, dependency freezes, or tests—to make a code change appear correct. Any proposed modification to a governed artifact requires a formal Change Request, a description of the business impact, and explicit user approval before implementation.

---

## Change Management & Release Gates

### Document Metadata Standard
Every freeze document must begin with a standardized header indicating its exact governance baseline:
```text
Document: [Document Name]
Version: 1.0
Governance Version: 1.0
Status: Frozen
Parent Constitution: 1.0
Effective Date: [Date]
```

### Change Impact Levels
Every PR or AI modification must declare its classification upfront:
*   **Level 0 (Cosmetic):** Comments, Formatting (No Approval Required)
*   **Level 1 (Infrastructure):** Performance, Caching (No Approval Required unless outputs differ)
*   **Level 1 (Type A Bug Fix):** Implementation bug (e.g., incorrect SQL, memory leak) with no intended behavior change.
*   **Level 2 (Data Layer):** Validation, Providers (Requires Change Request)
*   **Level 3 (Type B Bug Fix):** Business behavior bug (e.g., wrong RSI threshold, wrong validation rule) causing behavioral shift. (Requires Change Request)
*   **Level 3 (Business Logic):** Scanner logic, Scoring (Requires Change Request)
*   **Level 4 (Architecture):** DB Schema, Contracts (Requires Change Request)

### Governance Around Deletions
Deleting any of the following requires the exact same approval level as modifying them: Business Rules, Contracts, Architecture sections, Golden datasets, Snapshots, Invariants, Validation rules, Scanner stages, Database tables.

### Formal Change Request Workflow (Level 2-4)
Any behavioral change requires a formal payload containing: Change ID & Classification, Affected Rule IDs & Files, Expected Behavior Change & Production Impact, Backward Compatibility & Rollback Plan, and Tests/Snapshots Affected.

### Emergency Override Process
For urgent deployments: `Emergency Override` → `Temporary Approval` → `Deploy` → `48-hour Post-Mortem Review` → `Update Freezes` → `Regenerate Baselines`.

### Policy vs. Enforcement
*   **Policy:** Golden snapshots require explicit approval before modification; The Constitution version must match the release.
*   **Enforcement:** The CI/CD pipeline blocks snapshot updates without approval and technically enforces the following Final Release Gates before deployment is permitted:
    1.  **Governance Compliance:** PASS
    2.  **Architecture Freeze:** PASS
    3.  **Contracts Freeze:** PASS
    4.  **Business Rules Freeze:** PASS
    5.  **Dependency Compatibility:** PASS
    6.  **Golden Tests:** PASS
    7.  **Invariant Tests:** PASS
    8.  **Performance Regression:** PASS
    9.  **Behavior Manifest:** NO UNAPPROVED CHANGES
    10. **Change Request Review:** APPROVED
    11. **DEPLOY**

### The Governance Manifest
Every release must generate an audit artifact logging exactly what baseline was used and what was impacted:
```text
Release X.Y.Z
Governance Version: 1.0
Architecture Version: 1.0
Business Rules: 1.0
Contracts: 1.0
Dependencies: 1.0
Rules Changed: 0
Snapshots Changed: 0
Behavior Changes: 0
Status: SAFE TO DEPLOY
```

---

## The Implementation Roadmap

The Constitution is enforced through a strict 5-Phase project execution:

*   **Phase 1 — Governance Foundation:** Create the Constitution, Architecture Freeze, Contracts Freeze, Business Rules Freeze, and Dependency Freeze documents.
*   **Phase 2 — Traceability:** Assign Rule IDs throughout the codebase (`Rule -> Code -> Test -> Golden Dataset -> Manifest`). No behavior changes permitted.
*   **Phase 3 — Verification:** Build the Invariants, Golden datasets, Snapshot protection mechanisms, and Behavioral manifest scripts.
*   **Phase 4 — Enforcement:** Configure CI to reject unauthorized Rule changes, snapshot updates, contract changes, missing Rule IDs, and orphan tests.
*   **Phase 5 — Production:** Deploy the Governance pipeline (`Governance -> Development -> Testing -> Deployment`).
