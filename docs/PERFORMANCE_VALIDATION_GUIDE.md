# Performance Validation Guide

This document defines the governance rules, operational steps, and lifecycle for optimizing the Elite Breakout System. The Performance Validation Framework guarantees that optimizations improve speed without degrading memory, architecture, or business correctness.

## 1. The Optimization Approval Workflow

Ad-hoc optimizations are prohibited. Every optimization must pass through this rigorous lifecycle:

1. **Proposal**: Identify the bottleneck (using baseline profiling).
2. **Baseline**: Ensure a current baseline report exists (`reports/YYYY-MM-DD/`).
3. **Implement**: Write the optimized code.
4. **Compile & Lint**: Standard syntax checks.
5. **Architecture Validation**: Ensures the change doesn't violate rules (e.g., adding mutable module state).
6. **Business Regression**: The optimizer MUST pass against the frozen Golden Snapshots (`tests/fixtures/golden/`). Outputs must be identically matched.
7. **Performance Validation**: Compare new runtime against the versioned `performance_budget_v1.py`.
8. **Memory Validation**: Ensure peak RSS and GC metrics haven't surged.
9. **Determinism**: Prove that 3 consecutive runs produce identical results.
10. **Approval & Merge**: The PR is merged only with a clean `PASS` matrix.

## 2. Running the Framework

### 2.1 The CI Suite (Fast)
To validate a change during active development, run the fast smoke test (100 symbols):
```bash
python3 -m tests.performance.test_regression
python3 -m tests.performance.test_determinism
```
*Note: This automatically uses frozen datasets.*

### 2.2 The Nightly Suite (Deep)
Before a major release, run the heavy scalability and memory stress tests:
```bash
python3 -m tests.performance.test_scaling
python3 -m tests.performance.test_memory_stability
```

## 3. Managing Golden Snapshots

Golden Snapshots (`tests/fixtures/golden/`) act as binding behavioral contracts. 
- **Rule 1**: They are NEVER updated automatically by the framework.
- **Rule 2**: They are only updated when the Business Logic is *intentionally* changed (e.g., altering the scoring formula). 
- **To Update**: Manually run the snapshot generator script and commit the resulting `.json` and `.parquet` files along with the business logic PR.

## 4. Interpreting Reports

The framework outputs a Markdown report with a strict `PASS/WARNING/FAIL` matrix:
- **PASS**: The component operated within budget and matched contracts exactly.
- **WARNING**: The component operated near the edge of its budget (e.g., Scoring took 24s against a 25s limit).
- **FAIL**: A contract was broken (Data mismatch, OOM, Architecture violation). A `FAIL` halts the optimization pipeline immediately.
