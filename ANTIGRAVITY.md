# ANTIGRAVITY SYSTEM INSTRUCTIONS & ENGINEERING POLICIES

This file defines the mandatory engineering policies and workflow rules for all software development, code refactoring, system architecture modifications, bug fixes, performance optimizations, and maintenance tasks within the **Elite Breakout System** repository.

Violation of any rule means the task is **INCOMPLETE** and constitutes a process failure.

## 🚨 RULE 0 — Mandatory First Step: Check Rules & Create Implementation Plan (MANDATORY)

Before starting any analysis, code investigation, research, or code edit:
1. **Check & Read Workspace Rules Knowledge Item**: Always review the Knowledge Item summaries and read `/knowledge/workspace_rules/artifacts/rules.md`.
2. **Always Create / Update Implementation Plan First**: Create or update `implementation_plan.md` artifact detailing findings, root cause, and proposed changes, setting `request_feedback: true`.
3. **Obtain Explicit User Approval**: STOP and wait for the user's explicit approval before executing any code changes or modifying commands.

---

## 🚨 HIGHEST-PRIORITY RULE — Review First, Implement Later (MANDATORY)


### Purpose
Any review, audit report, bug report, optimization suggestion, AI-generated analysis, or user-provided recommendation is **NOT an implementation instruction**.

It is an input for technical evaluation only.

Implementation must **NEVER** begin until an independent engineering review has been completed and the user explicitly approves the implementation plan.

This rule has **NO EXCEPTIONS**.

---

### Phase 1 — Independent Technical Review (MANDATORY)

Whenever the user or external tools provide:
- Code Review
- Audit Report
- Root Cause Analysis (RCA)
- Bug Report
- Performance Report
- Security Finding
- External Recommendation
- Optimization Proposal
- Design Suggestion

the AI **MUST NOT modify code**.

Instead, it must perform its own independent engineering review based strictly on the current source code under `app/`. The external report is only supporting evidence.

#### Required Review Output
The AI must produce a structured review containing:

1. **Current Implementation Analysis**: Describe how the current implementation actually works in source code, referencing specific modules and line numbers.
2. **Independent Technical Assessment**: For every finding provide one of:
   - ✅ **Agree**
   - ⚠️ **Partially Agree**
   - ❌ **Disagree**  
   Each decision must include technical reasoning. Blind agreement or disagreement is prohibited.
3. **Root Cause Analysis (RCA)**: For every confirmed issue explain why it happens, where it originates, affected modules, architectural vs implementation factors, and configuration contribution.
4. **Repository-Wide Impact Analysis**: Search the entire repository to determine every caller, dependency, duplicate implementation, shared utility, affected scanner, configuration, API, and database interaction. A fix must never be evaluated in isolation.
5. **Risk Assessment**: Explain regression risks, performance impact, memory impact, thread safety, behavioral impact, backward compatibility, and production risk.
6. **Recommended Solution**: Explain the preferred engineering solution, comparing alternatives and trade-offs.
7. **Detailed Implementation Plan**: List specific files to modify, functions affected, dependency impact, documentation impact, unit tests required, and rollback considerations.

*No code changes are allowed during Phase 1.*

---

### Phase 2 — Mandatory User Approval

After presenting the review and implementation plan, **STOP**.

**Wait for explicit user approval.** Examples of approval: *"Proceed"*, *"Implement"*, *"Go ahead"*, *"Apply changes"*.

Without explicit approval:
- Do **NOT** modify code.
- Do **NOT** generate patches or edit files.
- Do **NOT** update documentation.
- Do **NOT** change unit tests.

*Silence or continuation of the conversation is NOT approval.*

---

### Phase 3 — Implementation

Only after explicit user approval may implementation begin. Implementation must follow all existing engineering rules below:

---

## 🕒 RULE 1 — IST Timezone Enforcement (MANDATORY)
The entire system operates exclusively in **Indian Standard Time (IST)**. Market Open (`09:00 AM IST`), Market Close (`03:30 PM IST`). Never use local machine time or UTC for business logic.

---

## 🔍 RULE 2 — Mandatory Impact Analysis Before Every Fix
Never perform isolated fixes. Find every caller, consumer, dependency, configuration, and database/API interaction affected before writing code.

---

## 📚 RULE 3 — Documentation Synchronization (MANDATORY)
Documentation is a first-class project artifact. Before every commit and Git push, update canonical documentation under `docs/` (`README.md`, `SYSTEM_ARCHITECTURE.md`, `SYSTEM_SPECIFICATION.md`). Code and documentation must always remain 100% synchronized.

---

## 🚀 RULE 4 — Build & Runtime Validation Before Push
A commit is **NOT** complete until the project is verified to be runnable. Verify compilation (`compileall app/`), imports, syntax, Flask initialization, Scheduler initialization, and database pool readiness.

---

## 🛡️ RULE 5 — Test Protection Policy
Unit tests are protected assets. **Never modify, weaken, bypass, remove, or rewrite unit tests** simply to make them pass without explicit user approval.

---

## 🔎 RULE 7 — Global Pattern Search for Bugs
Never assume a bug exists in only one location. Use `grep_search` across the entire repository to identify and fix every duplicate occurrence globally.

---

## 🛠️ RULE 8 — Root Cause Before Code Changes
Never patch symptoms. Identify and document the true root cause before writing code changes.

---

## 📋 RULE 10 — Documented Parameter Rationale (MANDATORY)
Every configurable threshold, risk cap, indicator period, or scanner parameter in `app/config.py` MUST have an explicit documented rationale.

For each parameter, documentation must specify:
1. **Why the parameter exists**: Purpose in technical or risk architecture.
2. **Chosen Value & Baseline**: Empirical or institutional origin (e.g. standard literature, backtest calibration, institutional risk limits).
3. **Evaluated Alternatives**: Other threshold values evaluated and trade-offs considered.
4. **Behavioral Impact**: Expected effect of raising or lowering the threshold on alert volume, win rate, and expectancy.

---

## 📢 RULE 12 — Mandatory Explanation of Changes (RCA & Rationale)
For EVERY code modification, bug fix, refactoring, or optimization, the AI MUST explicitly provide a detailed technical explanation to the user in its response, covering:
1. **Root Cause Analysis (RCA)**: Empirical evidence and technical reason for the issue.
2. **Exact Implementation Details**: Specific code, files, or configuration changes made.
3. **Behavioral Impact & Verification Proof**: Quantitative impact, test results, and validation proof.

---

## 🏆 RULE 13 — Mandatory Test & Golden Gate Coverage for New Features (MANDATORY)
Whenever adding any new feature, subsystem, quantitative engine, or architectural component, it **MUST** be fully covered by:
1. **Dedicated Unit & Integration Test Suites**: High-coverage test suite under `tests/` testing valid, invalid, boundary, and fallback conditions.
2. **Golden Gate & Production Deployment Gates**: Direct integration into `tests/test_production_deployment_gates.py` (Release Gates) and `tests/test_golden_rules.py` so that any future code modification or regression impacting the feature is automatically blocked at CI level.

---

## 📝 RULE 14 — Mandatory Documentation Synchronization & Deprecation Protocol (MANDATORY)
Whenever any feature, rule, schema, or configuration is added, modified, or replaced:
1. **All 4 Canonical Documents Updated**: Update `docs/UPCOMING_IMPROVEMENTS.md`, `docs/SYSTEM_SPECIFICATION.md`, `docs/SYSTEM_RECONSTRUCTION_SPEC.md`, and `ANTIGRAVITY.md` in lockstep.
2. **Deprecation Protocol (Markdown Strike-Through `~~`)**: Replaced or deprecated rules/features MUST NOT be silently deleted. They MUST use Markdown strike-through formatting (`~~old rule/feature~~`) accompanied by an explicit deprecation annotation: `*(Replaced on YYYY-MM-DD by <new_feature>)*`.


---

## ✅ RULE 11 — Definition of Done
A task is complete **ONLY IF ALL** conditions are satisfied:
- [ ] Root cause identified
- [ ] Current implementation reviewed
- [ ] Independent technical assessment completed
- [ ] Impact analysis completed
- [ ] Implementation plan prepared
- [ ] Explicit user approval received
- [ ] Implementation completed
- [ ] Parameter rationale documented (Rule 10)
- [ ] Detailed technical explanation of changes shared with user (Rule 12)
- [ ] Dedicated unit tests & Golden Gate coverage added for new features (Rule 13)
- [ ] Similar issues searched globally across repository
- [ ] All affected modules reviewed
- [x] Code compiles successfully
- [x] Application starts successfully
- [x] Tests pass (305 / 305 tests passing)
- [x] Documentation updated & synchronized
- [x] No regression introduced
- [x] Ready for Git push


