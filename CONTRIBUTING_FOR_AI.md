# CONTRIBUTING_FOR_AI

This document outlines the **non-negotiable rules** for any AI system or agent operating on the Elite Breakout System codebase. You must read and abide by these rules before generating code, refactoring logic, or running tests.

## 0. Test Integrity Policy (MANDATORY)
Tests are the specification of the system, not something to modify in order to make a build pass.

**An AI agent must never modify a test merely because it is failing.** A failing test is evidence that either the implementation is wrong or the business requirement has intentionally changed. The AI must not assume the latter without explicit user confirmation.

### Rules
1. **DO NOT modify, weaken, delete, skip, or disable any test solely to make a failing build pass.**
2. **DO NOT modify any file under `tests/`, `tests/fixtures/`, or `tests/golden/` without explicit user approval.**
3. If a test fails, **assume the production code is incorrect first.** Investigate the root cause and fix the implementation if the business behavior has not intentionally changed.
4. Tests may only be modified when **the user explicitly approves a business or architectural change** that changes the expected behavior.
5. If you believe a test is incorrect or outdated, **Do NOT change it automatically.** Explain why the test is failing, why you believe it is no longer valid, and which business rule has changed. Wait for explicit user approval before modifying the test.
6. **Golden snapshots, fixtures, and baseline files are immutable.** Never regenerate or overwrite them automatically. If they differ, produce a diff and explain the reason. Wait for user approval before accepting a new baseline.
7. Every production bug fix should include a new regression test or strengthen an existing one so the same bug cannot silently reappear.

### Decision Rule
When a test fails, follow this order:
1. Fix the production code.
2. Re-run the tests.
3. If the test still appears incorrect, stop and ask for approval before changing any test.

**Changing tests is the last resort, never the first.**

## 1. Business Rules and Thresholds
- **Never change business rule thresholds without explicit human approval.** (e.g., `MIN_SCORE = 72`, `vol_z_score >= 3.0`).
- **Never edit watchlist filtering logic** without approval.
- **Never modify stop-loss or target generation algorithms** without simultaneously updating the behavioral regression tests to match.

## 2. Data Contracts
- **Never rename dictionary keys or model fields.** (e.g., changing `entry` to `entry_price`, or `stop` to `stop_loss`). The frontend dashboard, database schemas, and multiple subsystems tightly couple to these explicit names.
- **Preserve public interfaces** unless a versioned change (e.g., schema v1 to v2) is explicitly intentional and approved.
- The `Opportunity` object is immutable in its key structure.
- The `regime_ctx` object must always contain `trend, biases, policy`.

## 3. Testing Environment (The "Fort Knox" Rules)
- **No Internet in Tests:** The test suite (`pytest`) operates under a strict Zero Network Policy. Tests must NEVER hit Yahoo Finance, NSE, BSE, or any external API. All test data must be loaded from `tests/fixtures/`.
- **No Randomness in Tests:** Tests must be perfectly deterministic. If utilizing randomness for property-based tests, strict seeds must be used.
- **Snapshot Immutability:** Never overwrite a versioned Golden Snapshot (e.g., `market_snapshot_v1`). If the strategy intentionally changes, generate a `v2` baseline and document the reason. Never update golden snapshots automatically; snapshot changes require explicit review.
- **Always Explain Diffs:** If an AI change causes a snapshot diff, the AI must halt and explicitly explain the diff (and why it is expected) to the user before proceeding.

## 4. UI and Frontend Policies (MANDATORY)
1. **No Disruptive Background Polling:** Never place `window.scrollTo`, `location.reload`, or focus-stealing logic inside `setInterval` or background data refresh loops (like `doRefresh`). 
2. **Preserve User State:** Background updates must mutate the DOM in-place (e.g., updating a table row or a metric) seamlessly without disrupting the user's current scroll position, selected text, or form inputs.
3. **Graceful Degradation:** If an API endpoint fails, the UI must gracefully log the error without causing infinite refresh loops or blinding the user with repeated error modals.

## 5. General Development
- **No Silent Degration:** If a fallback mechanism fails (e.g., `.BO` fetch fails), fail gracefully but loudly. Do not suppress errors or introduce infinite retry loops.
- **Regression Tests Required:** Every bug fix must include or update at least one regression test (unit, behavioral, or snapshot update) that explicitly breaks under the old logic and passes under the new logic.
- **Code Duplication:** Prefer extending existing business rules over introducing duplicate logic in new modules.

By enforcing these boundaries, we protect the production pipeline from silent regressions and accidental feedback loops.
