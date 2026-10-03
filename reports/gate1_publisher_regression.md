# Gate 1 — Publisher Regression

**Canonical symbols:** 886
**Canonical SHA256:** `8c1940bb9364c945...`

## Test 1A — Negative (Degraded Candidate)
- Decision: `BLOCKED`
- Canonical SHA unchanged: `True`
- Gate violations: ['Symbol set: 295 symbols lost (3MINDIA, AAVAS, ABB, ABBOTINDIA, ABSLAMC, ADOR, ADVENZYMES, AEROENTER, AEROFLEX, AETHER...)', 'Row count: 886 -> 591', 'Current EV/EBITDA complete: 811 -> 327', '3Y EV/EBITDA median complete: 879 -> 352', 'Current PE complete: 886 -> 355', '3Y PE median complete: 885 -> 590', 'Revenue complete: 831 -> 555', 'EBITDA complete: 831 -> 555', 'Net Profit complete: 886 -> 591', 'CFO complete: 885 -> 591', 'Total Debt complete: 829 -> 555', 'Cash complete: 859 -> 572', 'Shares complete: 886 -> 591', 'ROCE 5Y complete: 810 -> 329', 'Filing coverage complete: 886 -> 591', 'CERTIFIED provenance: 642 -> 302']
- **Result: PASS ✅**

## Test 1B — Positive (Improved Candidate)
- Decision: `PUBLISHED`
- **Result: PASS ✅**

## Gate 1 Verdict: ✅ PASS — Proceed to Gate 2