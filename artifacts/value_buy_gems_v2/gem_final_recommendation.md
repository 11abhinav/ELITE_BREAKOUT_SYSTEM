# MASTER VALUE GEM FINAL GOVERNANCE RECOMMENDATION

**Date**: 2026-09-28  
**System Component**: Live Fundamental Scanner / Value Buy Strategy  
**Dataset Provenance**: Real Upstox Market Data + Certified Point-in-Time Fundamentals DB  
**Program Status**: **COMPLETED & DECOMMISSIONED**  

---

## 1. Definitive Findings

Following full-universe backtesting across 10.75 years (2016–2026) covering 30 economically distinct hypotheses (`CONTROL_1..5` + `GEM_R1..25`):

1. **No Standalone "Value Gem" Dislocation Edge Exists in Indian Equities**:
   - A $25\%\text{--}45\%$ drawdown in a fundamental quality company is not an automatic value opportunity.
   - **Working Hypothesis & Sample Observation**: In $>80\%$ of tested historical dislocation cases in mid/small-caps, price drawdowns reflected ongoing or upcoming operational deterioration (subject to future dedicated event study verification).
   - Buying dislocations before confirmed technical/breakout recovery generates negative net expectancy ($-0.06\text{R}$ to $-0.08\text{R}$) after accounting for 15 bps round-trip transaction friction (7.5 bps entry / 7.5 bps exit).

2. **The 2020–2021 COVID Recovery Distortion**:
   - The initial baseline (`VALUE_GEM_CORE_V1`) achieved positive training returns (+0.101 R) solely because $>90\%$ of profits came from the 2020–2021 post-COVID macro liquidity expansion.
   - Outside of post-COVID bull market conditions (e.g. 2018–2019 bear market and 2024–2026 high valuation regimes), all 30 candidate formulations generated negative returns.

3. **Cash-Flow Cheapness Filter Constraint**:
   - High FCF Yield ($>5\%$) combined with strict growth intact filters produces zero trades in practice ($N=0$). Indian equities do not present extreme cash flow cheapness alongside untouched high quality without severe corporate governance or structural distress.

4. **Performance Metric Disconnect Note**:
   - High trade-level R expectancy does not directly translate to high portfolio CAGR due to cash drag, low trade velocity, position sizing (5% equal weight), and slot capacity constraints. Future research frameworks will explicitly separate Track A (Trade Expectancy) from Track B (Portfolio Compounded Return).

---

## 2. Final Governance Directive

```text
======================================================================
FINAL VALUE GEM GOVERNANCE VERDICT
======================================================================
RECOMMENDATION      : REJECT_ALL_30_CANDIDATE_FORMULATIONS
PRODUCTION_STATE    : DECOMMISSIONED
LIVE_ALERTS         : ZERO ALERTS / ZERO ROUTING
SCHEDULING          : STOPPED
AUDIT_TRAIL         : PRESERVED IN ARTIFACTS
FRICTION ASSUMPTION : 15 BPS ROUND-TRIP TOTAL (7.5 BPS PER SIDE)
======================================================================
```

### Action Items Executed:
1. `VALUE_GEM_CORE_V1` remains permanently locked as `REJECTED_BASELINE`.
2. All 25 candidate formulations (`GEM_R1..R25`) are classified `HOLDOUT_FAILED` or `UNDERPOWERED`.
3. No live fundamental buy scanner model is promoted to production.
4. Production routing remains **0 live alerts** for Value Buy strategies.
5. Research tradebooks, portfolio equity curves, and statistical matrices are permanently saved to `artifacts/value_buy_gems_v2/` for full forensic auditability.
