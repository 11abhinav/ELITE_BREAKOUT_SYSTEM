# ELITE BREAKOUT SYSTEM — QUANTITATIVE TRADING ENGINE (V8.1)

An enterprise-grade, automated quantitative trading engine and real-time market scanner optimized for National Stock Exchange (NSE) equity securities.

---

## 📚 Canonical System Documentation

The Elite Breakout System maintains a single, authoritative documentation set reconstructed directly from source implementation under `app/`:

- **[System Architecture Specification](docs/Architecture.md)** (`docs/Architecture.md`) — System overview, execution lifecycle, scheduler, scanner architecture, data flow, DB schema, threading, and Mermaid diagrams.
- **[Implementation Specification](docs/Implementation_Spec.md)** (`docs/Implementation_Spec.md`) — Comprehensive module inventory, public interfaces, dataclasses, enums, business rules, and helper functions.
- **[Deployment Verification & Release Pipeline](docs/Deployment_Verification.md)** (`docs/Deployment_Verification.md`) — 10-Stage Release Approval Pipeline, 13 Automated Deployment Gates, and Pre-Push Readiness Checklist.
- **[Documentation Drift & Audit Report](docs/Documentation_Drift.md)** (`docs/Documentation_Drift.md`) — Verified reconciliation audit comparing actual code behavior against historical assumptions.

---

## 🚀 Quick Start & Release Verification

### Run Automated Release Gates
```bash
python3 -m pytest tests/test_production_deployment_gates.py
```

### Run Full Test Suite
```bash
python3 -m pytest
```

### Build & Run Web Application
```bash
python3 app/main.py
```

---

## 🔒 Security & Release Integrity
Every commit is validated through 13 automated deployment gates including cold-start execution checks, AST reflection signature audits, memory budget checks ($< 450.0$ MB), and API version endpoint validation (`GET /version`).