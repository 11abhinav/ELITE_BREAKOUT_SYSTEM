# Performance Manifest

This document serves as the canonical inventory of all validations, collectors, budgets, and governance rules enforced by the Performance Validation Framework.

## 1. Collectors (Data Gathering)
| Collector | Purpose | File |
| :--- | :--- | :--- |
| `PipelineTracer` | End-to-end latency timing of scanner phases. | `collectors/pipeline_tracer.py` |
| `MemoryCollector` | RSS delta and peak memory allocation tracking. | `collectors/memory_collector.py` |
| `ProviderCollector` | Latency, retries, and failures by provider (Fyers, Yahoo, NSE). | `collectors/provider_collector.py` |
| `GCCollector` | Object generation counts and GC pause durations. | `collectors/gc_collector.py` |
| `DatasetCollector`| DatasetRegistry hit rates, latency, and refresh timing. | `collectors/dataset_collector.py` |

## 2. Validators (Policy Enforcement)
| Validator | Purpose | File |
| :--- | :--- | :--- |
| `BusinessValidator` | Asserts exact equivalence against Golden Snapshots. | `validators/business_validator.py` |
| `ContractValidator` | Asserts Dataset invariants (schema, dtypes, timezone). | `validators/contract_validator.py` |
| `PerformanceValidator` | Asserts execution times against versioned performance budgets. | `validators/performance_validator.py` |
| `ArchitectureValidator`| Asserts isolation rules (e.g., no JSON loads in inner loops). | `validators/architecture_validator.py` |
| `DeterminismValidator` | Asserts 3 consecutive runs yield identical outputs. | `validators/determinism_validator.py` |
| `MemoryValidator` | Asserts peak RSS and GC pauses against memory budgets. | `validators/memory_validator.py` |

## 3. Analyzers & Reporters
| Component | Purpose | File |
| :--- | :--- | :--- |
| `ScalingAnalyzer` | Identifies O(n) vs O(n^2) scaling characteristics. | `analyzers/scaling_analyzer.py` |
| `TrendAnalyzer` | Compares current run vs historical runs in `reports/`. | `analyzers/trend_analyzer.py` |
| `MarkdownReport` | Generates the final PASS/WARNING/FAIL matrix. | `reporters/markdown_report.py` |

## 4. Test Suites
| Suite | Purpose | Execution |
| :--- | :--- | :--- |
| **CI Smoke Test** | Fast regression. Runs on 100 symbols. | Every PR |
| **Scaling Test** | Validates 100 -> 5,000 symbol scaling. | Nightly / Pre-Release |
| **Memory Stability** | Simulates 30 days of Session Rotations. | Nightly / Pre-Release |

## 5. Golden Snapshots (Contracts)
Located in `tests/fixtures/golden/`:
- `watchlist.json`
- `indicators.parquet`
- `scanner_candidates.parquet`
- `rankings.parquet`
- `alerts.json`
- `sl_target.json`
