# THREE-REGIME PRODUCTION CERTIFICATION REPORT
## EOD / PULLBACK / ACCUMULATION / TECHNICAL
**Audit Timestamp:** 2026-09-26 19:12:08 IST  
**Execution Runtime:** 182.79 seconds  
**Macro Market Dataset:** 10-Year Nifty Breadth (937 BEAR, 850 BULL, 677 SIDEWAYS trading days)  
**Bootstrap Replications:** 10,000 (Clustered by Symbol, Seed: 20261001)  
**Permutation Replications:** 10,000 (Paired Sign-Flip, Seed: 20261002)  
**Transaction Friction:** 5 bps entry notional + 5 bps exit notional per executed leg  

---

## 1. Authoritative 4 × 3 Production Routing Matrix

| Scanner | BULL Macro Regime | SIDEWAYS Macro Regime | BEAR Macro Regime | Production Routing Decision |
| :--- | :---: | :---: | :---: | :--- |
| **PULLBACK** | `FAIL` | `PASS` | `FAIL` | **ZERO PRODUCTION ALERTS (UNDER_CERTIFICATION / DECOMMISSIONED)** |
| **TECHNICAL** | `PASS` | `FAIL` | `FAIL` | **BULL-ONLY PRODUCTION ALERTS** |
| **ACCUMULATION** | `FAIL` | `FAIL` | `PASS` | **ZERO PRODUCTION ALERTS (UNDER_CERTIFICATION / DECOMMISSIONED)** |
| **EOD** | `FAIL` | `FAIL` | `FAIL` | **ZERO PRODUCTION ALERTS (UNDER_CERTIFICATION / DECOMMISSIONED)** |

---

## 2. Granular Performance by Scanner × Regime

### 2.2 Scanner: PULLBACK

| Macro Regime | Trades Evaluated | Arm A Net R [95% CI] | Arm B Net R [95% CI] | Delta (B - A) [95% CI] | Perm p | Holdout Trades | Holdout B Net R [95% CI] | Holdout Delta [95% CI] | Regime Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **BULL** | 16,504 | +0.1570R [+0.1323, +0.1809] | **+0.1796R** [+0.1588, +0.2001] | **+0.0227R** [+0.0114, +0.0340] | 0.00010 | 1,042 | [-0.0743, +0.0980] | [-0.0167, +0.0705] | **`NOT_CERTIFIED`** |
| **SIDEWAYS** | 15,796 | +0.0729R [+0.0486, +0.0974] | **+0.1092R** [+0.0876, +0.1303] | **+0.0363R** [+0.0250, +0.0471] | 0.00010 | 2,545 | [+0.0429, +0.1507] | [+0.0286, +0.0813] | **`CERTIFIED_FOR_PRODUCTION`** |
| **BEAR** | 11,555 | +0.0582R [+0.0302, +0.0868] | **+0.0782R** [+0.0524, +0.1035] | **+0.0200R** [+0.0074, +0.0327] | 0.00010 | 1,558 | [-0.0379, +0.1037] | [+0.0211, +0.1055] | **`NOT_CERTIFIED`** |

### 2.4 Scanner: TECHNICAL

| Macro Regime | Trades Evaluated | Arm A Net R [95% CI] | Arm B Net R [95% CI] | Delta (B - A) [95% CI] | Perm p | Holdout Trades | Holdout B Net R [95% CI] | Holdout Delta [95% CI] | Regime Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **BULL** | 11,575 | +0.1565R [+0.1294, +0.1823] | **+0.2176R** [+0.1951, +0.2400] | **+0.0611R** [+0.0474, +0.0753] | 0.00010 | 566 | [+0.1114, +0.2938] | [+0.0186, +0.1510] | **`CERTIFIED_FOR_PRODUCTION`** |
| **SIDEWAYS** | 5,864 | +0.0931R [+0.0556, +0.1318] | **+0.1444R** [+0.1135, +0.1755] | **+0.0513R** [+0.0318, +0.0713] | 0.00010 | 1,042 | [-0.0214, +0.1294] | [-0.0147, +0.0764] | **`NOT_CERTIFIED`** |
| **BEAR** | 2,927 | +0.1085R [+0.0573, +0.1597] | **+0.1354R** [+0.0938, +0.1754] | **+0.0269R** [-0.0006, +0.0550] | 0.02190 | 406 | [+0.0134, +0.2230] | [-0.1079, +0.0527] | **`NOT_CERTIFIED`** |

### 2.3 Scanner: ACCUMULATION

| Macro Regime | Trades Evaluated | Arm A Net R [95% CI] | Arm B Net R [95% CI] | Delta (B - A) [95% CI] | Perm p | Holdout Trades | Holdout B Net R [95% CI] | Holdout Delta [95% CI] | Regime Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **BULL** | 7,031 | +0.1880R [+0.1358, +0.2430] | **+0.1855R** [+0.1411, +0.2320] | **-0.0025R** [-0.0242, +0.0196] | 1.00000 | 530 | [-0.2762, -0.0309] | [-0.0294, +0.0953] | **`NOT_CERTIFIED`** |
| **SIDEWAYS** | 4,336 | +0.0418R [-0.0145, +0.0980] | **+0.0925R** [+0.0434, +0.1421] | **+0.0507R** [+0.0240, +0.0774] | 0.00020 | 997 | [-0.1283, +0.0655] | [+0.0500, +0.1600] | **`NOT_CERTIFIED`** |
| **BEAR** | 3,134 | +0.0477R [-0.0203, +0.1193] | **+0.1019R** [+0.0405, +0.1664] | **+0.0542R** [+0.0227, +0.0857] | 0.00020 | 504 | [+0.0743, +0.3050] | [+0.0064, +0.1651] | **`CERTIFIED_FOR_PRODUCTION`** |

### 2.1 Scanner: EOD

| Macro Regime | Trades Evaluated | Arm A Net R [95% CI] | Arm B Net R [95% CI] | Delta (B - A) [95% CI] | Perm p | Holdout Trades | Holdout B Net R [95% CI] | Holdout Delta [95% CI] | Regime Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **BULL** | 968 | +0.0737R [-0.0005, +0.1494] | **+0.1540R** [+0.0864, +0.2216] | **+0.0803R** [+0.0354, +0.1248] | 0.00030 | 67 | [-0.2388, +0.2872] | [-0.0191, +0.2898] | **`NOT_CERTIFIED`** |
| **SIDEWAYS** | 560 | +0.0233R [-0.0739, +0.1197] | **+0.0840R** [-0.0011, +0.1670] | **+0.0606R** [+0.0060, +0.1176] | 0.01980 | 120 | [-0.1814, +0.1851] | [-0.0704, +0.1621] | **`NOT_CERTIFIED`** |
| **BEAR** | 375 | +0.1061R [-0.0194, +0.2300] | **+0.1140R** [+0.0053, +0.2181] | **+0.0079R** [-0.0526, +0.0686] | 0.40466 | 64 | [-0.1023, +0.4496] | [-0.2139, +0.0833] | **`NOT_CERTIFIED`** |

---

## 3. Production Governance Directives

1. **Zero Provisional/Shadow States:** Scanners have exactly three permissible states: `CERTIFIED_FOR_PRODUCTION`, `UNDER_CERTIFICATION`, and `DECOMMISSIONED`.
2. **Strict Production Alert Permission:** Only scanner × regime combinations with `PASS` in the routing matrix are authorized to generate live production trade alerts.
3. **Automatic Fail-Closed Security:** The production engine strictly asserts that no decommissioned or uncertified scanner may produce live signals.
