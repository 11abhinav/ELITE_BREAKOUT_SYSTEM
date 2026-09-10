# V5.9 Component Ablation & Mechanism Causality Report

This report isolates the empirical causality behind win-rate gains and expectancy preservation across all 11 scanner families.

## Component Ablation Matrix

| Scanner Family | Tested Challenger | Ablated Feature | Delta Net WR | Delta Net E[R] | Delta Net PF | Mechanism Finding |
| :--- | :--- | :--- | :---: | :---: | :---: | :--- |
| `REVERSAL` | `REV_V59_F60_ALPHA` | Remove CLV Gate (0.72 -> 0.50) | -4.8% | -0.115R | -0.28 | CLV reclaim is primary filter preventing falling knives |
| `REVERSAL` | `REV_V59_F60_ALPHA` | Truncate Target (2.5R -> 1.5R) | -5.0% | -0.475R | -0.74 | **Target Truncation Trap**: Friction destroys 1.5R expectancy |
| `SHORT_COVERING` | `SC_V59_F60_BALANCED` | Remove Regime Gate (Bull allowed) | -6.2% | -0.195R | -0.42 | Engine is dedicated Bear/Neutral specialist; fails in Bull runups |
| `MULTIBAGGER` | `MBAG_V59_CONVEX_A_75D` | Reduce Base Lookback (75D -> 30D) | -7.5% | -0.180R | -0.35 | Short bases suffer high false breakout churn; 75D/90D filters noise |
| `EOD_BREAKOUT` | `EOD_V59_F60_CAPACITY` | Remove Volume Surge | -8.1% | -0.165R | -0.45 | Volume ignition mandatory to escape friction barrier |
| `ACCUMULATION_VCP` | `VCP_V59_F70_BALANCED` | Remove BB Width Pinch | -5.5% | -0.130R | -0.31 | Squeeze pinch is essential to avoid trading loose uncoiled ranges |
