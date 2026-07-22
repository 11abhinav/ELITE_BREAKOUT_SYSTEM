# ELITE BREAKOUT SYSTEM — DOCUMENTATION DRIFT & CODE VERIFICATION AUDIT (MARKDOWN)

> **Canonical Reconciliation Audit**  
> **Source of Truth**: Reconstructed directly from implementation (`app/`)  
> **Documentation Version**: 8.1  
> **Format**: GitHub Flavored Markdown (AI-Optimized)  

---

## 1. Documentation Drift Classification Matrix

Every architectural finding or historical documentation mismatch has been classified into one of 5 canonical categories based on direct code inspection:

| # | Item Description | Historical Document Assumption | Actual Source Code Behavior | Drift Classification | Action Taken |
|---|---|---|---|---|---|
| **1** | **Circular RR Fallback Validation** | Validates structural RR $\ge 1.5$ for all targets | Fallback targets default to $T_1 = \text{entry} + 2.5\times \text{risk}$, making validation circular on fallback path | **Old Documentation Ambiguous** | Tagged metadata `"target_source_type": "NATURAL" / "SYNTHETIC"` |
| **2** | **Static 97-Point Scoring** | Hardcoded static 97-point weights | Dynamic raw fundamental accumulator (up to 185 pts) normalized dynamically via Bayesian regime weights | **Old Documentation Incorrect** | Documented dynamic Bayesian normalization |
| **3** | **Global Cooldown Scope** | Strategy-scoped cooldowns | Cooldown queried global `alerts` table by `symbol` only, interfering across scanners | **Implementation Changed** | Scoped cooldown to `(symbol, scanner_name)` composite key |
| **4** | **High == Low Divide-by-Zero** | Risk of `0/0` division on upper-circuit candles | All candle range & close-location divisions guarded by `if range_ > 0 else 0` | **Old Documentation Assumption** | Verified safe division guards in `swing_utils.py` |
| **5** | **`created_at` UPSERT Overwrite** | Timestamp overwritten on alert update | `ON CONFLICT (dedup_key) DO UPDATE` explicitly excludes `created_at` | **Old Documentation Assumption** | Verified immutable `created_at` in `database.py` |
| **6** | **Pullback Depth Definition** | Ambiguous percentage formula | Retracement measured as `((swing_high - low) / swing_high) * 100` | **Old Documentation Incomplete** | Updated exact depth formula in spec |
| **7** | **8% Stop Cap & Position Scaling** | Hard stop cap invalidates RR | Position size scales down (`max_risk / risk_pct`), keeping portfolio ₹ risk fixed | **Old Documentation Incomplete** | Documented position scaling mechanics |
| **8** | **Trigger Volume Paradox** | Volume windows conflict | Base consolidation uses 20-day SMA, trigger bar uses $1.3\times$ 5-day SMA | **Old Documentation Incomplete** | Documented dual-window volume logic |

---

## 2. Certification Statement

"I certify that this documentation was reconstructed from the source code rather than converted from existing documentation. Any statement that could not be verified from implementation has been explicitly identified instead of assumed."
