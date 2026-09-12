# PULLBACK CONTINUATION SCANNER MASTER TOURNAMENT REPORT
## Empirical Evaluation Across 869 Real NSE/BSE Parquet Symbols (6 Variants x 6 Market Regimes)
**Strict Point-in-Time Causality ($T \le t$, Zero Lookahead) | Zero Dummy Data**

### 1. Overall Combined Leaderboard (All 6 Market Regimes Combined)

| Rank | Variant ID | Description | Trades | Win Rate % | Profit Factor | Expectancy E[R] | Total R | Max DD (R) | SQN | Avg Risk % | Post-SL Suffocation % |
| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **#1** | `PULLBACK_CHALL_D_ADAPTIVE` | Challenger D: Volatility-Regime Adaptive ATR Stop | 15876 | 20.21% | 1.44 | **+0.2270R** | **+3604.0R** | 111.9R | 20.06 | 4.42% | **4.15%** |
| **#2** | `PULLBACK_CHALL_B_1_8ATR` | Challenger B: 1.8 x ATR_14 Clamped [4.5%, 7.5%] | 15876 | 14.47% | 1.46 | **+0.2128R** | **+3379.0R** | 97.9R | 20.47 | 5.06% | **3.39%** |
| **#3** | `PULLBACK_CHALL_A_1_5ATR` | Challenger A: 1.5 x ATR_14 Stop | 15876 | 29.43% | 1.33 | **+0.2007R** | **+3186.1R** | 114.7R | 16.0 | 3.30% | **13.01%** |
| **#4** | `PULLBACK_CHALL_C_2ATR_SHELF` | Challenger C: 2.0 x ATR_14 + Anchored Under Swing Shelf | 15876 | 11.31% | 1.5 | **+0.1969R** | **+3126.7R** | 90.8R | 20.91 | 6.47% | **2.4%** |
| **#5** | `PULLBACK_CHAMPION_V1` | Baseline Fixed 5.0% Stop (Suffocation Prone) | 15876 | 13.31% | 1.42 | **+0.1926R** | **+3057.6R** | 89.7R | 18.92 | 5.00% | **5.8%** |
| **#6** | `PULLBACK_CHALL_E_CONFIRMED_TURN` | Challenger E: Volatility-Adaptive Stop + Bullish Turn Confirmation Filter | 4184 | 15.42% | 1.12 | **+0.0658R** | **+275.4R** | 135.7R | 3.01 | 5.13% | **4.69%** |

### 2. Breakdown by Macro Market Regime Category

#### Macro Regime: **BULL**

| Variant ID | Trades | Win Rate % | Profit Factor | Expectancy E[R] | Total R | Post-SL Suffocation % |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `PULLBACK_CHALL_D_ADAPTIVE` | 7610 | 22.56% | 1.72 | **+0.3458R** | +2631.9R | 3.98% |
| `PULLBACK_CHALL_B_1_8ATR` | 7610 | 16.83% | 1.8 | **+0.3358R** | +2555.5R | 3.64% |
| `PULLBACK_CHALL_A_1_5ATR` | 7610 | 32.14% | 1.51 | **+0.2989R** | +2274.5R | 15.06% |
| `PULLBACK_CHALL_C_2ATR_SHELF` | 7610 | 13.71% | 1.91 | **+0.3249R** | +2472.7R | 2.55% |
| `PULLBACK_CHAMPION_V1` | 7610 | 15.49% | 1.75 | **+0.3092R** | +2352.9R | 7.42% |
| `PULLBACK_CHALL_E_CONFIRMED_TURN` | 1718 | 22.0% | 1.69 | **+0.3484R** | +598.5R | 5.13% |

#### Macro Regime: **NEUTRAL**

| Variant ID | Trades | Win Rate % | Profit Factor | Expectancy E[R] | Total R | Post-SL Suffocation % |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `PULLBACK_CHALL_D_ADAPTIVE` | 5306 | 20.66% | 1.44 | **+0.2323R** | +1232.3R | 4.13% |
| `PULLBACK_CHALL_B_1_8ATR` | 5306 | 13.34% | 1.44 | **+0.2047R** | +1086.3R | 2.36% |
| `PULLBACK_CHALL_A_1_5ATR` | 5306 | 31.1% | 1.36 | **+0.2249R** | +1193.2R | 11.88% |
| `PULLBACK_CHALL_C_2ATR_SHELF` | 5306 | 9.82% | 1.46 | **+0.1816R** | +963.5R | 1.51% |
| `PULLBACK_CHAMPION_V1` | 5306 | 11.85% | 1.44 | **+0.2002R** | +1062.3R | 2.95% |
| `PULLBACK_CHALL_E_CONFIRMED_TURN` | 1418 | 13.12% | 0.9 | **-0.0606R** | -86.0R | 5.23% |

#### Macro Regime: **BEAR**

| Variant ID | Trades | Win Rate % | Profit Factor | Expectancy E[R] | Total R | Post-SL Suffocation % |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `PULLBACK_CHALL_D_ADAPTIVE` | 2960 | 13.38% | 0.85 | **-0.0879R** | -260.2R | 4.55% |
| `PULLBACK_CHALL_B_1_8ATR` | 2960 | 10.44% | 0.84 | **-0.0888R** | -262.8R | 4.52% |
| `PULLBACK_CHALL_A_1_5ATR` | 2960 | 19.46% | 0.85 | **-0.0951R** | -281.6R | 10.09% |
| `PULLBACK_CHALL_C_2ATR_SHELF` | 2960 | 7.84% | 0.79 | **-0.1045R** | -309.5R | 3.39% |
| `PULLBACK_CHAMPION_V1` | 2960 | 10.3% | 0.79 | **-0.1208R** | -357.6R | 6.91% |
| `PULLBACK_CHALL_E_CONFIRMED_TURN` | 1048 | 7.73% | 0.62 | **-0.2263R** | -237.2R | 3.27% |


### 3. Individual 6-Period Temporal Regime Breakdown

#### Period `BULL_1_EXPANSION`: H1 2025 Broad Bull Expansion (BULL) [2025-01-01 to 2025-06-30]

| Variant ID | Trades | Win Rate % | Profit Factor | Expectancy E[R] | Total R | Post-SL Suffocation % |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `PULLBACK_CHALL_D_ADAPTIVE` | 2871 | 20.48% | 1.78 | **+0.3543R** | +1017.3R | 1.8% |
| `PULLBACK_CHALL_B_1_8ATR` | 2871 | 11.74% | 1.94 | **+0.3409R** | +978.6R | 0.11% |
| `PULLBACK_CHALL_A_1_5ATR` | 2871 | 33.23% | 1.47 | **+0.2862R** | +821.7R | 16.04% |
| `PULLBACK_CHALL_C_2ATR_SHELF` | 2871 | 10.21% | 2.09 | **+0.3307R** | +949.3R | 0.72% |
| `PULLBACK_CHAMPION_V1` | 2871 | 7.8% | 1.96 | **+0.3156R** | +906.0R | 0.0% |
| `PULLBACK_CHALL_E_CONFIRMED_TURN` | 428 | 20.56% | 1.43 | **+0.2261R** | +96.8R | 3.26% |

#### Period `BULL_2_MOMENTUM`: Spring/Summer 2026 Momentum Rally (BULL) [2026-04-01 to 2026-07-31]

| Variant ID | Trades | Win Rate % | Profit Factor | Expectancy E[R] | Total R | Post-SL Suffocation % |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `PULLBACK_CHALL_D_ADAPTIVE` | 4739 | 23.82% | 1.69 | **+0.3407R** | +1614.6R | 5.17% |
| `PULLBACK_CHALL_B_1_8ATR` | 4739 | 19.92% | 1.73 | **+0.3327R** | +1576.9R | 5.21% |
| `PULLBACK_CHALL_A_1_5ATR` | 4739 | 31.48% | 1.53 | **+0.3066R** | +1452.8R | 14.43% |
| `PULLBACK_CHALL_C_2ATR_SHELF` | 4739 | 15.83% | 1.83 | **+0.3215R** | +1523.4R | 3.32% |
| `PULLBACK_CHAMPION_V1` | 4739 | 20.15% | 1.66 | **+0.3053R** | +1446.9R | 10.25% |
| `PULLBACK_CHALL_E_CONFIRMED_TURN` | 1290 | 22.48% | 1.78 | **+0.3890R** | +501.8R | 5.77% |

#### Period `BEAR_1_SELLOFF`: Late Winter 2026 Market Correction (BEAR) [2026-02-15 to 2026-03-31]

| Variant ID | Trades | Win Rate % | Profit Factor | Expectancy E[R] | Total R | Post-SL Suffocation % |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `PULLBACK_CHALL_D_ADAPTIVE` | 1620 | 15.12% | 0.92 | **-0.0485R** | -78.6R | 5.16% |
| `PULLBACK_CHALL_B_1_8ATR` | 1620 | 9.26% | 0.88 | **-0.0715R** | -115.8R | 5.67% |
| `PULLBACK_CHALL_A_1_5ATR` | 1620 | 23.58% | 0.88 | **-0.0861R** | -139.6R | 12.49% |
| `PULLBACK_CHALL_C_2ATR_SHELF` | 1620 | 6.6% | 0.79 | **-0.1146R** | -185.6R | 4.57% |
| `PULLBACK_CHAMPION_V1` | 1620 | 7.9% | 0.8 | **-0.1156R** | -187.3R | 8.7% |
| `PULLBACK_CHALL_E_CONFIRMED_TURN` | 478 | 6.9% | 0.42 | **-0.4527R** | -216.4R | 4.08% |

#### Period `BEAR_2_DRAWDOWN`: Late Summer 2026 Sharp Drawdown (BEAR) [2026-08-01 to 2026-09-11]

| Variant ID | Trades | Win Rate % | Profit Factor | Expectancy E[R] | Total R | Post-SL Suffocation % |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `PULLBACK_CHALL_D_ADAPTIVE` | 1340 | 11.27% | 0.74 | **-0.1355R** | -181.6R | 3.56% |
| `PULLBACK_CHALL_B_1_8ATR` | 1340 | 11.87% | 0.78 | **-0.1097R** | -147.0R | 2.73% |
| `PULLBACK_CHALL_A_1_5ATR` | 1340 | 14.48% | 0.81 | **-0.1060R** | -142.0R | 6.01% |
| `PULLBACK_CHALL_C_2ATR_SHELF` | 1340 | 9.33% | 0.79 | **-0.0924R** | -123.9R | 1.43% |
| `PULLBACK_CHAMPION_V1` | 1340 | 13.21% | 0.76 | **-0.1271R** | -170.3R | 4.43% |
| `PULLBACK_CHALL_E_CONFIRMED_TURN` | 570 | 8.42% | 0.92 | **-0.0364R** | -20.8R | 1.88% |

#### Period `NEUTRAL_1_SUMMER_RANGE`: Summer 2025 Sideways Consolidation (NEUTRAL) [2025-07-01 to 2025-09-15]

| Variant ID | Trades | Win Rate % | Profit Factor | Expectancy E[R] | Total R | Post-SL Suffocation % |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `PULLBACK_CHALL_D_ADAPTIVE` | 1791 | 26.91% | 2.24 | **+0.5318R** | +952.4R | 2.96% |
| `PULLBACK_CHALL_B_1_8ATR` | 1791 | 17.14% | 2.43 | **+0.5052R** | +904.8R | 0.68% |
| `PULLBACK_CHALL_A_1_5ATR` | 1791 | 39.92% | 1.97 | **+0.5193R** | +930.1R | 14.38% |
| `PULLBACK_CHALL_C_2ATR_SHELF` | 1791 | 12.62% | 2.44 | **+0.4324R** | +774.5R | 0.67% |
| `PULLBACK_CHAMPION_V1` | 1791 | 14.52% | 2.56 | **+0.5103R** | +914.0R | 0.38% |
| `PULLBACK_CHALL_E_CONFIRMED_TURN` | 337 | 19.88% | 1.78 | **+0.3665R** | +123.5R | 3.27% |

#### Period `NEUTRAL_2_WINTER_CHOP`: Winter 2025-26 Rotation & Choppy Range (NEUTRAL) [2025-11-01 to 2026-01-31]

| Variant ID | Trades | Win Rate % | Profit Factor | Expectancy E[R] | Total R | Post-SL Suffocation % |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `PULLBACK_CHALL_D_ADAPTIVE` | 3515 | 17.47% | 1.14 | **+0.0796R** | +280.0R | 4.58% |
| `PULLBACK_CHALL_B_1_8ATR` | 3515 | 11.41% | 1.1 | **+0.0516R** | +181.5R | 2.92% |
| `PULLBACK_CHALL_A_1_5ATR` | 3515 | 26.6% | 1.11 | **+0.0748R** | +263.1R | 10.84% |
| `PULLBACK_CHALL_C_2ATR_SHELF` | 3515 | 8.39% | 1.12 | **+0.0538R** | +189.0R | 1.78% |
| `PULLBACK_CHAMPION_V1` | 3515 | 10.5% | 1.08 | **+0.0422R** | +148.4R | 3.76% |
| `PULLBACK_CHALL_E_CONFIRMED_TURN` | 1081 | 11.01% | 0.71 | **-0.1938R** | -209.5R | 5.65% |
