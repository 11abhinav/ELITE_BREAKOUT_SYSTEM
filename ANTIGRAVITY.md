# ANTIGRAVITY SYSTEM INSTRUCTIONS & ENGINEERING POLICIES

This file defines the mandatory engineering policies and workflow rules for all software development, code refactoring, system architecture modifications, bug fixes, performance optimizations, and maintenance tasks within the **Elite Breakout System** repository.

Violation of any rule means the task is **INCOMPLETE**.

---

## 🕒 RULE 1 — IST Timezone Enforcement (MANDATORY)

The entire system operates exclusively in **Indian Standard Time (IST)**.

### Requirements:
- Never use local machine time.
- Never use UTC for business logic.
- Never hardcode another timezone.
- All scheduling, timestamps, cron windows, logging, scanner execution, cooldown calculations, market session validation, and reports **must use IST**.
- Market timings are fixed as:
  - Market Open: **09:00 AM IST**
  - Market Close: **03:30 PM IST**
- Any logic dependent on market time must validate against these timings unless explicitly documented otherwise.

---

## 🔍 RULE 2 — Mandatory Impact Analysis Before Every Fix

Never perform isolated fixes.

Whenever changing any function, class, configuration, constant, API, algorithm, or business rule:
1. Find every caller.
2. Find every consumer.
3. Find every dependency.
4. Find every configuration using it.
5. Find every database/API interaction affected.
6. Evaluate behavioral impact.
7. Verify no regressions are introduced.

Do not assume a change is local. Every modification must include a complete downstream impact analysis before implementation.

---

## 📚 RULE 3 — Documentation Synchronization (MANDATORY)

Documentation is a first-class project artifact.

Before every commit and Git push:
- Update all affected canonical documentation (`docs/README.md`, `docs/SYSTEM_ARCHITECTURE.md`, `docs/SYSTEM_SPECIFICATION.md`).
- Update architecture diagrams if needed.
- Update implementation contracts.
- Update configuration tables.
- Update scheduler documentation.
- Update API documentation.
- Update glossary if terminology changes.
- Update ADRs if architectural decisions change.

Code and documentation must always remain synchronized.

---

## 🚀 RULE 4 — Build & Runtime Validation Before Push

A commit is **NOT** complete until the project is verified to be runnable.

Before every push verify:
- Project compiles successfully (`compileall app/`).
- All Python modules import correctly.
- No circular imports, missing imports, or dependency issues.
- No syntax errors or runtime startup errors.
- Application starts successfully.
- All major components initialize successfully (Scheduler, Flask server, Database connection pool, Background workers).
- No startup exceptions.

Never push code that has not been validated end-to-end.

---

## 🛡️ RULE 5 — Test Protection Policy

Unit tests are protected assets.

Never modify:
- Unit Tests
- Integration Tests
- Regression Tests
- Fort Knox Tests
- Smoke Tests

...unless explicitly instructed by the user.

If a test fails:
1. Fix production code first.
2. Explain why the test failed.
3. Modify tests **only after explicit user approval**.

Never weaken, bypass, remove, or rewrite tests simply to make them pass.

---

## 🧠 RULE 6 — Independent Technical Review Before Implementation

Every user request requires an independent engineering review. Do not blindly implement requested changes.

Before writing code:
1. Inspect the current implementation.
2. Understand existing architecture.
3. Verify assumptions against the current code.
4. Perform your own technical analysis.
5. Compare the proposal with the existing implementation.

Then provide:
- Current implementation summary
- Your independent technical assessment
- Agree / Partially Agree / Disagree with rationale
- Identified risks
- Alternative approaches (if any)
- Detailed implementation plan

Only begin coding after completing this review.

---

## 🔎 RULE 7 — Global Pattern Search for Bugs

Never assume a bug exists in only one location.

Whenever fixing a bug:
1. Search the entire repository for the same coding pattern using `grep_search`.
2. Identify every duplicate implementation.
3. Review every occurrence.
4. Fix every confirmed occurrence.
5. Verify consistency across all modules.

Examples: Incorrect formula, wrong comparison operator, timezone bug, null handling bug, resource leak, retry logic, thread safety issue, risk calculation, configuration misuse.

A bug fixed in one file but left elsewhere is considered an **incomplete implementation**.

---

## 🛠️ RULE 8 — Root Cause Before Code Changes

Never patch symptoms.

Before implementing a fix:
- Identify the true root cause.
- Explain why it occurred.
- Explain why previous logic failed.
- Explain why the new logic resolves the issue.
- Consider edge cases.
- Validate that the fix does not introduce regressions.

---

## ✅ RULE 9 — Definition of Done

A task is complete **ONLY IF ALL** conditions are satisfied:

- [ ] Root cause identified
- [ ] Current implementation reviewed
- [ ] Independent technical assessment completed
- [ ] Impact analysis completed
- [ ] Implementation completed
- [ ] Similar issues searched across repository
- [ ] All affected modules reviewed
- [ ] Code compiles successfully
- [ ] Application starts successfully
- [ ] Dependencies validated
- [ ] Tests pass (271 / 271)
- [ ] Documentation updated
- [ ] Architecture synchronized
- [ ] Comments added for business logic changes
- [ ] No regression introduced
- [ ] Ready for Git push

If any item above is incomplete, the task is **INCOMPLETE**.
