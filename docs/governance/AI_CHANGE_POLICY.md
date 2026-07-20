Document: AI_CHANGE_POLICY.md
Version: 1.0
Governance Version: 1.0
Status: Frozen
Parent Constitution: 1.0
Effective Date: 2026-07-20

# AI Governance Policy

This policy is a legally binding contract for any AI assistant or autonomous agent operating within the Elite Breakout System codebase. It enforces the separation between code optimization and business rule mutation.

## 1. Verbatim AI Rule

An AI must never modify any governed artifact—including architecture documents, business rules, contracts, golden datasets, snapshots, invariants, dependency freezes, or tests—to make a code change appear correct. Any proposed modification to a governed artifact requires a formal Change Request, a description of the business impact, and explicit user approval before implementation.

## 2. Prohibition on Silent Mutations

The AI SHALL NOT:
*   ❌ Change tests or update snapshots to make them pass following a code modification.
*   ❌ Modify scanner thresholds, scoring constants, or Stop-Loss logic.
*   ❌ Modify provider priority or business rules.
*   ❌ Swallow exceptions or insert fallback `except:` blocks that mask upstream failures.

## 3. Required Behavior on Test Failure

If a code modification made by the AI causes any behavioral snapshot or invariant test to fail, the AI must **STOP** execution and produce a **Behavior Changed Report**:

1.  **Affected Rule:** [Rule ID]
2.  **Reason for Change:** [Detailed explanation of why the underlying code drifted]
3.  **Behavior Difference:** [What exactly changed in the snapshot/output]
4.  **Production Impact:** [Estimated impact on alert volume, scoring, or SL]

The AI must then wait for explicit `USER APPROVAL` before taking any further action to modify tests or update golden snapshots.

## 4. Inherent Deference to Specification

The AI must recognize that **the business specification is authoritative**. Golden datasets and snapshots are approved verification artifacts that demonstrate conformance to the specification; they do not define intended behavior. 

If the AI discovers a conflict between the code implementation and the stated `BUSINESS_RULES.md` specification, the AI must align the code to the specification, never the other way around.
