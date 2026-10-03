# Gate 2 — Smoke Test Log Verifier

**Log file:** `logs/scanner_smoke_test.log`
**Analysis date:** 2026-10-03 16:27:37 IST

## Required Patterns (must appear ≥ 1x)
| Pattern | Count | Status |
|---|---|---|
| `[FUNDAMENTAL_PRE_RECOVERY] START` | 1 | ✅ |

## Exactly-Once Patterns (must appear exactly 1x)
| Pattern | Count | Status |
|---|---|---|
| `[FUNDAMENTAL_PRE_RECOVERY] START` | 1 | ✅ |

## Forbidden Patterns (must appear 0x)
| Pattern | Count | Status |
|---|---|---|
| `JIT_PERSISTED` | 0 | ✅ |
| `JIT_SUCCESS` | 0 | ✅ |
| `JIT_BUDGET_EXHAUSTED` | 0 | ✅ |
| `Saving updated canonical PIT directly` | 0 | ✅ |
| `canonical_pit_rebuilt.parquet written by pre_recovery` | 0 | ✅ |
| `scanner-level network fundamental fetch` | 0 | ✅ |
| `[PRE_RECOVERY] Saving updated canonical PIT dataset` | 0 | ✅ |

## Gate 2 Verdict: ✅ PASS — Proceed to Gate 3