# REPORT 3: EOD Necessity Controlled Experiment (2022–2026 YTD)

Holding 5m Intraday Ignition Logic STRICTLY CONSTANT (5m Score >= 65.0, OI <= -0.50%, Uncapped):

| Pipeline Tier | EOD Filter | Trades (N) | Win Rate (%) | Expectancy (E[R]) | Total Return (R) | Profit Factor | Max Drawdown | +5R Rate (%) |
|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **E0** | **No EOD (Active Universe)** | **7455** | **97.96%** | **+1.087R** | **+8103.3R** | **54.31** | **2.0R** | **1.31%** |
| **E1** | EOD Score >= 20 | 7455 | 97.96% | +1.087R | +8103.3R | 54.31 | 2.0R | 1.31% |
| **E2** | EOD Score >= 30 | 3667 | 98.01% | +1.0867R | +3984.8R | 55.59 | 2.0R | 1.47% |
| **E3** | EOD Score >= 35 (V9 Level) | 3346 | 98.12% | +1.0839R | +3626.77R | 58.57 | 2.0R | 1.61% |
| **E4** | EOD Score >= 40 (V10 Level) | 2419 | 97.77% | +1.0689R | +2585.58R | 48.88 | 2.0R | 1.41% |
| **E5** | EOD Score >= 50 (Prod Baseline) | 1500 | 97.13% | +1.0109R | +1516.34R | 36.26 | 2.0R | 1.4% |

## Empirical Findings:
1. **No Quality Degradation**: Removing EOD entirely (E0) yields 97.96% WR and +1.087R expectancy vs 97.13% WR and +1.0109R in E5.
2. **Huge Opportunity Expansion**: Uncapping and removing the strict EOD gate unlocked 5955 additional high-expectancy trades over the 4.7-year period.
