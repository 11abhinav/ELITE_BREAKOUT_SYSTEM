# MULTIPLE-TESTING CORRECTION REPORT

- Total Hypotheses Tested ($M$): **36**
- Nominal Alpha: **0.05**
- Bonferroni Threshold: $\alpha / M = 0.05 / 36 = 0.001389$
- Benjamini-Hochberg False Discovery Rate (FDR): **$q = 0.05$**

| Combination | Raw Holdout p | Bonferroni Passed | BH FDR Passed | Status |
| :--- | :---: | :---: | :---: | :---: |
| `ACC-V01-BASE × BULL` | 1.00000 | False | False | `FAIL` |
| `ACC-V01-BASE × SIDEWAYS` | 1.00000 | False | False | `FAIL` |
| `ACC-V01-BASE × BEAR` | 0.00050 | True | True | `FAIL` |
| `ACC-V02-VOL-SURGE × BULL` | 0.43250 | False | False | `FAIL` |
| `ACC-V02-VOL-SURGE × SIDEWAYS` | 0.47250 | False | False | `FAIL` |
| `ACC-V02-VOL-SURGE × BEAR` | 0.29350 | False | False | `FAIL` |
| `ACC-V03-TREND-STRENGTH × BULL` | 1.00000 | False | False | `FAIL` |
| `ACC-V03-TREND-STRENGTH × SIDEWAYS` | 0.01550 | False | False | `FAIL` |
| `ACC-V03-TREND-STRENGTH × BEAR` | 0.00500 | False | True | `FAIL` |
| `ACC-V04-VOLATILITY-ADAPTIVE × BULL` | 1.00000 | False | False | `FAIL` |
| `ACC-V04-VOLATILITY-ADAPTIVE × SIDEWAYS` | 1.00000 | False | False | `FAIL` |
| `ACC-V04-VOLATILITY-ADAPTIVE × BEAR` | 0.15000 | False | False | `FAIL` |
| `PB-V01-BASE × BULL` | 0.35500 | False | False | `FAIL` |
| `PB-V01-BASE × SIDEWAYS` | 0.00050 | True | True | `FAIL` |
| `PB-V01-BASE × BEAR` | 0.10150 | False | False | `FAIL` |
| `PB-V02-DEEP-SUPPORT × BULL` | 0.17450 | False | False | `FAIL` |
| `PB-V02-DEEP-SUPPORT × SIDEWAYS` | 0.00250 | False | True | `FAIL` |
| `PB-V02-DEEP-SUPPORT × BEAR` | 0.06350 | False | False | `FAIL` |
| `PB-V03-RSI-OVERSOLD × BULL` | 0.30700 | False | False | `FAIL` |
| `PB-V03-RSI-OVERSOLD × SIDEWAYS` | 0.00100 | True | True | `FAIL` |
| `PB-V03-RSI-OVERSOLD × BEAR` | 0.18550 | False | False | `FAIL` |
| `PB-V04-STRUCTURAL-PIVOT × BULL` | 0.04150 | False | False | `FAIL` |
| `PB-V04-STRUCTURAL-PIVOT × SIDEWAYS` | 0.00050 | True | True | `FAIL` |
| `PB-V04-STRUCTURAL-PIVOT × BEAR` | 0.27250 | False | False | `FAIL` |
| `EOD-V01-BASE × BULL` | 0.43750 | False | False | `FAIL` |
| `EOD-V01-BASE × SIDEWAYS` | 1.00000 | False | False | `FAIL` |
| `EOD-V01-BASE × BEAR` | 0.21600 | False | False | `FAIL` |
| `EOD-V02-52W-MOMENTUM × BULL` | 0.31200 | False | False | `FAIL` |
| `EOD-V02-52W-MOMENTUM × SIDEWAYS` | 0.25700 | False | False | `FAIL` |
| `EOD-V02-52W-MOMENTUM × BEAR` | 0.20250 | False | False | `FAIL` |
| `EOD-V03-COMPRESSION-EXPANSION × BULL` | 0.39950 | False | False | `FAIL` |
| `EOD-V03-COMPRESSION-EXPANSION × SIDEWAYS` | 1.00000 | False | False | `FAIL` |
| `EOD-V03-COMPRESSION-EXPANSION × BEAR` | 1.00000 | False | False | `FAIL` |
| `EOD-V04-MOMENTUM-CORRIDOR × BULL` | 0.31450 | False | False | `FAIL` |
| `EOD-V04-MOMENTUM-CORRIDOR × SIDEWAYS` | 1.00000 | False | False | `FAIL` |
| `EOD-V04-MOMENTUM-CORRIDOR × BEAR` | 1.00000 | False | False | `FAIL` |
