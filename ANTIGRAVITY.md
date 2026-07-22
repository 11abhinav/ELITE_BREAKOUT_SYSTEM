# ANTIGRAVITY SYSTEM INSTRUCTIONS & ENGINEERING POLICIES

This file defines the mandatory engineering policies and workflow rules for all software development, code refactoring, system architecture modifications, and maintenance tasks within the **Elite Breakout System** repository.

---

## 📚 Documentation Synchronization Rule (MANDATORY)

### 1. Documentation is Part of the Codebase
Documentation is a first-class project artifact. Any change to system behavior, architecture, business logic, configuration, APIs, database schema, scheduling, scanners, algorithms, deployment, or public interfaces **MUST** be reflected in the canonical documentation before any Git push.

This rule is **mandatory and has no exceptions**.

---

### 2. Required Documentation Updates
Whenever implementation changes, update the appropriate canonical documentation files before committing:

- [`docs/README.md`](docs/README.md)
- [`docs/SYSTEM_ARCHITECTURE.md`](docs/SYSTEM_ARCHITECTURE.md)
- [`docs/SYSTEM_SPECIFICATION.md`](docs/SYSTEM_SPECIFICATION.md)

Update whichever documents are affected by the change. Examples include (but are not limited to):

- New scanner or strategy
- Removed scanner or strategy
- Business rule changes
- Configuration constant changes
- Scheduler changes
- SL/Target logic modifications
- Database schema changes
- REST API changes
- Threading changes
- Deployment changes
- Error handling changes
- New architectural components
- Removal of architectural components
- New mathematical formulas
- Changes to scoring logic
- Changes to alert lifecycle
- Changes to cooldown logic
- Changes to data providers
- Changes to external integrations

---

### 3. Documentation Must Match the Code
- The implementation under `app/` is the single source of truth.
- Documentation must always describe the current implementation.
- Never leave documentation describing behavior that no longer exists.
- Never leave obsolete diagrams, configuration values, formulas, APIs, or scheduler information.

---

### 4. Architectural Change Requirements
Whenever an architectural decision changes:
- Update diagrams (System, Deployment, State Transition, Error Recovery).
- Update dependency trees.
- Update the Component Catalog table.
- Update Architecture Decision Records (ADR) when the design rationale changes.

---

### 5. Business Logic Change Requirements
Whenever business logic changes:
- Update the Implementation Contract in `SYSTEM_SPECIFICATION.md`.
- Update formulas and mathematical definitions.
- Update threshold values and configuration tables.
- Update the Traceability Matrix if affected.
- Update the System Glossary if new terminology is introduced.

---

### 6. Mandatory Code Comments for Logic Changes
Whenever existing logic is modified or replaced, add a clear code comment explaining:
1. What the previous logic was.
2. Why it was changed.
3. What limitation or issue existed in the previous implementation.
4. Why the new implementation is preferred.
5. Any expected behavioral differences.

**Example Code Comment**:
```python
# Previous Logic:
# Stop loss was ATR-only, which frequently placed stops below weak
# structural support levels and increased stop-out probability.
#
# Reason for Change:
# Replaced with Structural Support Engine to anchor stops to validated
# support clusters while still respecting the maximum risk cap.
#
# Expected Impact:
# Better alignment with institutional support levels and improved
# reward-to-risk consistency.
```
*Do not remove historical reasoning comments unless they are no longer relevant.*

---

### 7. Removed Logic Requirements
When removing existing functionality:
- Remove obsolete code, documentation, diagrams, configuration entries, and ADR references.
- Record what was removed, why it was removed, what replaced it (if anything), and the expected impact in commit messages or code comments.

---

### 8. Pre-Push Verification Checklist (MANDATORY)

Before every Git push, verify:
- [ ] Implementation is complete and syntax compiles cleanly (`compileall app/`).
- [ ] Automated test suite passes (`pytest`).
- [ ] Canonical documentation in `docs/` is updated.
- [ ] Diagrams reflect current architecture.
- [ ] Configuration tables are synchronized.
- [ ] API documentation is synchronized.
- [ ] Database documentation is synchronized.
- [ ] Commit hash metadata regenerated if applicable.
- [ ] Comments explaining logic changes have been added.
- [ ] Documentation contains no obsolete information.

---

### 9. Zero Documentation Drift Policy
Code and documentation **must evolve together**.

A commit that changes implementation without updating the required documentation is considered **incomplete** and must not be pushed until both are synchronized.

The canonical documentation under `docs/` must always represent the current implementation of the system.
