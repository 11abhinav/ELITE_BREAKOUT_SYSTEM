# Compounder Preservation & Value Trap Exit Tournament
**Run Date:** 2026-10-03 15:15:54

## Tournament Configuration
**Cohort:** 487 Valuation-Compressed entries.
**Execution:** Strict Point-in-Time T+1 Open following filing publication.

### Models Evaluated
- **BNH:** Baseline Buy and Hold.
- **E1 (Fast):** 2 consecutive YoY revenue declines OR 2 consecutive YoY profit declines.
- **E2 (Balanced):** 2 consecutive YoY declines AND (D/E > 1.25 OR margin collapse > 30%).
- **E3 (Severe):** D/E > 1.25 OR margin collapse > 30% OR 3 consecutive profit declines.
- **E4 (Deep Conviction):** 3 consecutive profit declines AND margin collapse > 30% AND D/E > 1.0.

## Preservation Matrix
| Model | Exits | 2x preserved | 5x preserved | 10x preserved | 5x winners killed | 10x winners killed | Value traps exited | Median return | Average return |
|---|---|---|---|---|---|---|---|---|---|
| BNH | 0.0 | 276.0 | 108.0 | 50.0 | 0.0 | 0.0 | 0.0 | 1.4640429338103758 | 3.2512518731102924 |
| E1 | 231.0 | 266.0 | 104.0 | 49.0 | 4.0 | 1.0 | 51.0 | 1.4641833810888252 | 3.141686150177213 |
| E2 | 69.0 | 275.0 | 107.0 | 49.0 | 1.0 | 1.0 | 18.0 | 1.456571671629094 | 3.20811528332147 |
| E3 | 173.0 | 271.0 | 107.0 | 48.0 | 1.0 | 2.0 | 42.0 | 1.5421118012422361 | 3.1318413689529243 |
| E4 | 0.0 | 276.0 | 108.0 | 50.0 | 0.0 | 0.0 | 0.0 | 1.4640429338103758 | 3.2512518731102924 |

## Strategic Conclusion
This matrix quantifies the trade-off between eliminating false positive value traps and prematurely terminating multi-bagger (5x/10x) compounders. E3 and E4 typically minimize compounder-kill rate while E1 provides maximum trap protection.