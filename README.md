# ELITE BREAKOUT SYSTEM — QUANTITATIVE TRADING ENGINE

An enterprise-grade, automated quantitative trading engine and real-time market scanner optimized for National Stock Exchange (NSE) equity securities.

---

## 📚 Canonical System Documentation

The Elite Breakout System maintains a single, authoritative documentation set consisting of **exactly two canonical Markdown files** reconstructed directly from source implementation under `app/`:

- **[SYSTEM ARCHITECTURE](docs/SYSTEM_ARCHITECTURE.md)** (`docs/SYSTEM_ARCHITECTURE.md`) — Answers *"What exists and how does it work?"* High-level architecture, component catalog, dependency tree, state transition lifecycle, wall-clock scheduler triggers, scanner engine flows, database schema, threading, and Mermaid diagrams.
- **[SYSTEM SPECIFICATION](docs/SYSTEM_SPECIFICATION.md)** (`docs/SYSTEM_SPECIFICATION.md`) — Answers *"Exactly how is it implemented?"* Complete engineering implementation contract for core architectural modules, detailing public/private APIs, algorithms, business rules, configuration reference appendix, API reference appendix, database operations appendix, and system glossary.

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

## 🔒 Verification & Release Integrity
Every commit is validated through 13 automated deployment gates including cold-start execution checks, AST reflection signature audits, memory budget checks ($< 450.0$ MB), and API version endpoint validation (`GET /version`). Documentation verified against commit `920de35e7eedd09231a93740b47b3f08e1548cdc`.