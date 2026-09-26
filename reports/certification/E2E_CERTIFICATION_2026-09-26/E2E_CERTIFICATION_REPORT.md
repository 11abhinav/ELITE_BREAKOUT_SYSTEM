# END-TO-END PRODUCTION CERTIFICATION REPORT
## EOD / PULLBACK / ACCUMULATION / TECHNICAL
**Audit Timestamp:** 2026-09-26 17:51:38 IST  
**Execution Runtime:** 751.98 seconds  
**Bootstrap Replications:** 10,000 (Clustered by Symbol, Seed: 20261001)  
**Permutation Replications:** 10,000 (Paired Sign-Flip, Seed: 20261002)  
**Transaction Cost Standard:** 5 bps entry notional + 5 bps exit notional per executed leg  

---

## 1. Executive Summary & Production Promotion Verdict

| Scanner | Operating Regime | Entries Evaluated | Arm A Mean Net R | Arm B Mean Net R | Paired Delta Mean (B - A) [95% CI] | Perm p-value | Final Verdict |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **EOD** | BULL | 1,768 | +0.0620R | +0.1246R | **+0.0626R** [+0.0307R, +0.0944R] | 0.00020 | **`FAIL`** |
| **PULLBACK** | BULL | 44,174 | +0.0991R | +0.1261R | **+0.0270R** [+0.0200R, +0.0338R] | 0.00010 | **`CERTIFIED_FOR_PRODUCTION`** |
| **ACCUMULATION** | BULL | 13,921 | +0.1266R | +0.1486R | **+0.0220R** [+0.0053R, +0.0391R] | 0.00030 | **`FAIL`** |
| **TECHNICAL** | OVERALL | 20,425 | +0.1310R | +0.1838R | **+0.0529R** [+0.0421R, +0.0637R] | 0.00010 | **`CERTIFIED_FOR_PRODUCTION`** |

---

## 2. Frozen Entry Ledgers & SHA256 Fingerprints

| Scanner | Source Ledger File | SHA256 Fingerprint | Raw Entries | Certified Enforced Filter | Clean Trades Evaluated |
| :--- | :--- | :--- | :---: | :--- | :---: |
| **EOD** | `ledger.csv` | `7f2b54fa5f23cb3b...` | 1,909 | `BULL` | 1,768 |
| **PULLBACK** | `ledger_stride1.csv` | `8e394bb2b050d58f...` | 44,174 | `BULL` | 44,174 |
| **ACCUMULATION** | `ledger_stride1.csv` | `b7b91fa7a577eb7d...` | 14,778 | `BULL` | 13,921 |
| **TECHNICAL** | `ledger_stride1.csv` | `d56fb796130b7055...` | 20,425 | `OVERALL` | 20,425 |

---

## 3. Arm A (Fixed Control) vs Arm B (Dynamic Exit) Detailed Breakdown

### 3.1 Scanner: EOD
- **Operating Regime:** `BULL` (Holding Cap: 10 bars)
- **Frozen Ledger Hash:** `7f2b54fa5f23cb3b552f87e42c56bfb9e633d1d7ab9257267aa32e3c2e8872fb`

#### Statistical Comparison by Data Slice

| Metric | Full Dataset | In-Sample (<2025-10-01) | Holdout (>=2025-10-01) |
| :--- | :---: | :---: | :---: |
| **Trades Evaluated** | 1,768 | 1,543 | 225 |
| **Symbols Count** | 653 | 597 | 192 |
| **Arm A Mean Net R** | +0.0620R | +0.0764R | -0.0366R |
| **Arm A 95% CI (Cluster Boot)** | [+0.0031R, +0.1205R] | [+0.0142R, +0.1391R] | [-0.1953R, +0.1256R] |
| **Arm A Win Rate** | 43.0% | 43.8% | 37.8% |
| **Arm B Mean Net R** | +0.1246R | +0.1412R | +0.0112R |
| **Arm B 95% CI (Cluster Boot)** | [+0.0721R, +0.1768R] | [+0.0864R, +0.1974R] | [-0.1261R, +0.1454R] |
| **Arm B Win Rate** | 53.2% | 54.0% | 47.6% |
| **Paired Delta Mean (B - A)** | **+0.0626R** | **+0.0648R** | **+0.0477R** |
| **Paired Delta Median** | +0.0000R | +0.0000R | +0.0000R |
| **Paired Delta 95% CI** | [+0.0307R, +0.0944R] | [+0.0304R, +0.0995R] | [-0.0395R, +0.1373R] |
| **Sign-Flip Permutation p** | **0.00020** | **0.00030** | **0.13979** |

#### Portfolio Level Dynamics
- **Annualized Sharpe Proxy:** `1.9506`
- **Daily Volatility (R):** `0.1804` (Annualized: `2.8643`)
- **Max Drawdown (R):** `8.1866`
- **Active Days:** `1519` / 1737 (12.6% cash drag)

#### Gate Evaluation & Promotion Check
- **Condition A (Arm B Holdout CI > 0):** `FAIL` ([-0.1261, 0.1454])
- **Condition B (Arm B Beats Control, CI > 0 and p < 0.05):** `PASS` (CI: [0.0307, 0.0944], p=0.0002)
- **Condition C (Causality & Data Integrity):** `PASS` (Strictly causal, same-bar stop precedence)
- **Condition D (Operating Regime Enforcement):** `PASS` (BULL)
- **Condition E (Audit Reproducibility):** `PASS` (Seeds 20261001 / 20261002, hash `7f2b54fa5f23cb3b`)
- **Final Verdict:** **`FAIL`**

---

### 3.2 Scanner: PULLBACK
- **Operating Regime:** `BULL` (Holding Cap: 10 bars)
- **Frozen Ledger Hash:** `8e394bb2b050d58ff69b548fbfafbb5ca1f169216f3b8177f512896b254e5183`

#### Statistical Comparison by Data Slice

| Metric | Full Dataset | In-Sample (<2025-10-01) | Holdout (>=2025-10-01) |
| :--- | :---: | :---: | :---: |
| **Trades Evaluated** | 44,174 | 38,986 | 5,188 |
| **Symbols Count** | 865 | 792 | 719 |
| **Arm A Mean Net R** | +0.0991R | +0.1114R | +0.0067R |
| **Arm A 95% CI (Cluster Boot)** | [+0.0837R, +0.1148R] | [+0.0949R, +0.1277R] | [-0.0411R, +0.0540R] |
| **Arm A Win Rate** | 45.5% | 46.0% | 41.9% |
| **Arm B Mean Net R** | +0.1261R | +0.1352R | +0.0573R |
| **Arm B 95% CI (Cluster Boot)** | [+0.1127R, +0.1397R] | [+0.1210R, +0.1494R] | [+0.0162R, +0.0982R] |
| **Arm B Win Rate** | 52.5% | 52.8% | 49.6% |
| **Paired Delta Mean (B - A)** | **+0.0270R** | **+0.0238R** | **+0.0506R** |
| **Paired Delta Median** | +0.0000R | +0.0000R | +0.0000R |
| **Paired Delta 95% CI** | [+0.0200R, +0.0338R] | [+0.0165R, +0.0314R] | [+0.0292R, +0.0719R] |
| **Sign-Flip Permutation p** | **0.00010** | **0.00010** | **0.00010** |

#### Portfolio Level Dynamics
- **Annualized Sharpe Proxy:** `3.5275`
- **Daily Volatility (R):** `0.0858` (Annualized: `1.3622`)
- **Max Drawdown (R):** `5.4519`
- **Active Days:** `1740` / 1740 (0.0% cash drag)

#### Gate Evaluation & Promotion Check
- **Condition A (Arm B Holdout CI > 0):** `PASS` ([0.0162, 0.0982])
- **Condition B (Arm B Beats Control, CI > 0 and p < 0.05):** `PASS` (CI: [0.02, 0.0338], p=0.0001)
- **Condition C (Causality & Data Integrity):** `PASS` (Strictly causal, same-bar stop precedence)
- **Condition D (Operating Regime Enforcement):** `PASS` (BULL)
- **Condition E (Audit Reproducibility):** `PASS` (Seeds 20261001 / 20261002, hash `8e394bb2b050d58f`)
- **Final Verdict:** **`CERTIFIED_FOR_PRODUCTION`**

---

### 3.3 Scanner: ACCUMULATION
- **Operating Regime:** `BULL` (Holding Cap: 14 bars)
- **Frozen Ledger Hash:** `b7b91fa7a577eb7d7b28bb3b141f1818d40899cf0569a90b7d36efe1bdd828f9`

#### Statistical Comparison by Data Slice

| Metric | Full Dataset | In-Sample (<2025-10-01) | Holdout (>=2025-10-01) |
| :--- | :---: | :---: | :---: |
| **Trades Evaluated** | 13,921 | 12,009 | 1,912 |
| **Symbols Count** | 733 | 683 | 464 |
| **Arm A Mean Net R** | +0.1266R | +0.1576R | -0.0683R |
| **Arm A 95% CI (Cluster Boot)** | [+0.0849R, +0.1703R] | [+0.1138R, +0.2048R] | [-0.1531R, +0.0175R] |
| **Arm A Win Rate** | 44.1% | 44.9% | 39.1% |
| **Arm B Mean Net R** | +0.1486R | +0.1719R | +0.0018R |
| **Arm B 95% CI (Cluster Boot)** | [+0.1136R, +0.1874R] | [+0.1341R, +0.2142R] | [-0.0741R, +0.0769R] |
| **Arm B Win Rate** | 54.1% | 54.9% | 49.1% |
| **Paired Delta Mean (B - A)** | **+0.0220R** | **+0.0143R** | **+0.0702R** |
| **Paired Delta Median** | +0.0000R | +0.0000R | +0.0000R |
| **Paired Delta 95% CI** | [+0.0053R, +0.0391R] | [-0.0038R, +0.0324R] | [+0.0321R, +0.1084R] |
| **Sign-Flip Permutation p** | **0.00030** | **0.01600** | **0.00010** |

#### Portfolio Level Dynamics
- **Annualized Sharpe Proxy:** `4.2944`
- **Daily Volatility (R):** `0.1099` (Annualized: `1.7443`)
- **Max Drawdown (R):** `4.0531`
- **Active Days:** `1682` / 1739 (3.3% cash drag)

#### Gate Evaluation & Promotion Check
- **Condition A (Arm B Holdout CI > 0):** `FAIL` ([-0.0741, 0.0769])
- **Condition B (Arm B Beats Control, CI > 0 and p < 0.05):** `PASS` (CI: [0.0053, 0.0391], p=0.0003)
- **Condition C (Causality & Data Integrity):** `PASS` (Strictly causal, same-bar stop precedence)
- **Condition D (Operating Regime Enforcement):** `PASS` (BULL)
- **Condition E (Audit Reproducibility):** `PASS` (Seeds 20261001 / 20261002, hash `b7b91fa7a577eb7d`)
- **Final Verdict:** **`FAIL`**

---

### 3.4 Scanner: TECHNICAL
- **Operating Regime:** `OVERALL` (Holding Cap: 12 bars)
- **Frozen Ledger Hash:** `d56fb796130b70559895d0aabdf88d4f5b0645416f2c48c0cc6e642cfd9793ad`

#### Statistical Comparison by Data Slice

| Metric | Full Dataset | In-Sample (<2025-10-01) | Holdout (>=2025-10-01) |
| :--- | :---: | :---: | :---: |
| **Trades Evaluated** | 20,425 | 18,391 | 2,034 |
| **Symbols Count** | 842 | 780 | 563 |
| **Arm A Mean Net R** | +0.1310R | +0.1375R | +0.0720R |
| **Arm A 95% CI (Cluster Boot)** | [+0.1095R, +0.1529R] | [+0.1143R, +0.1604R] | [+0.0071R, +0.1383R] |
| **Arm A Win Rate** | 43.2% | 43.5% | 40.9% |
| **Arm B Mean Net R** | +0.1838R | +0.1925R | +0.1052R |
| **Arm B 95% CI (Cluster Boot)** | [+0.1658R, +0.2024R] | [+0.1732R, +0.2120R] | [+0.0554R, +0.1549R] |
| **Arm B Win Rate** | 54.5% | 54.9% | 51.4% |
| **Paired Delta Mean (B - A)** | **+0.0529R** | **+0.0551R** | **+0.0332R** |
| **Paired Delta Median** | +0.0000R | +0.0000R | +0.0000R |
| **Paired Delta 95% CI** | [+0.0421R, +0.0637R] | [+0.0438R, +0.0668R] | [-0.0028R, +0.0683R] |
| **Sign-Flip Permutation p** | **0.00010** | **0.00010** | **0.01770** |

#### Portfolio Level Dynamics
- **Annualized Sharpe Proxy:** `4.0749`
- **Daily Volatility (R):** `0.112` (Annualized: `1.7782`)
- **Max Drawdown (R):** `6.576`
- **Active Days:** `1722` / 1740 (1.0% cash drag)

#### Gate Evaluation & Promotion Check
- **Condition A (Arm B Holdout CI > 0):** `PASS` ([0.0554, 0.1549])
- **Condition B (Arm B Beats Control, CI > 0 and p < 0.05):** `PASS` (CI: [0.0421, 0.0637], p=0.0001)
- **Condition C (Causality & Data Integrity):** `PASS` (Strictly causal, same-bar stop precedence)
- **Condition D (Operating Regime Enforcement):** `PASS` (OVERALL)
- **Condition E (Audit Reproducibility):** `PASS` (Seeds 20261001 / 20261002, hash `d56fb796130b7055`)
- **Final Verdict:** **`CERTIFIED_FOR_PRODUCTION`**

---

## 4. Governance Decision & Operational Guidelines

1. **ACCUMULATION Signal Suppression Rule:**
   - Enforce in production scanner dispatch: `if macro_regime == 'BEAR': suppress_signal()`.
   - Zero BEAR ACCUMULATION trades may enter live order execution.

2. **EOD / PULLBACK Operating Regime:**
   - Both certified strictly for `BULL` macro regime conditions.

3. **Exit Manager Execution Mandate:**
   - If Condition B PASSES for a scanner, Arm B (Production Dynamic Exit: Target Ladder 50%/50% + Breakeven at +1R + 0.5 ATR Trailing Stop) is authorized for production routing.
   - If Condition B FAILS (i.e. dynamic exit does not statistically outperform fixed control), Arm A (Fixed Control: 1.5 ATR Stop, 3.0 ATR Target, Holding Cap) remains the certified execution profile.

---
*(Report generated automatically by `scripts/run_end_to_end_certification.py`)*