# MANDATORY TEMPORAL REPLICATION & REGIME ROBUSTNESS REPORT

**Date:** 2026-09-26  
**Dataset Provenance:** UPSTOX Real Market Data Cache (SHA256 fingerprint verified)  
**Total Historical Trades Evaluated:** 80,288  
**Temporal Cells:** 4 Multi-Year non-overlapping cells + 4 Calendar Quarters  

## 1. Multi-Year & Quarterly Replication Matrix

| Scanner | Regime | Total N | 2016-18 | 2019-21 | 2022-24 | 2025-26 | Q1 | Q2 | Q3 | Q4 | Flags | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TECHNICAL | BULL | 11575 | +0.220R (N=1652) | +0.233R (N=3472) | +0.221R (N=5443) | +0.146R (N=1008) | +0.206R | +0.296R | +0.134R | +0.261R | NONE | CERTIFIED_FOR_PRODUCTION |
| TECHNICAL | SIDEWAYS | 5864 | +0.214R (N=649) | +0.223R (N=1455) | +0.137R (N=2398) | +0.040R (N=1362) | +0.039R | +0.263R | +0.210R | +0.056R | NONE | NOT_CERTIFIED |
| TECHNICAL | BEAR | 2927 | -0.020R (N=512) | +0.320R (N=628) | +0.133R (N=990) | +0.093R (N=797) | -0.069R | +0.284R | +0.306R | +0.144R | NONE | NOT_CERTIFIED |
| PULLBACK | BULL | 16504 | +0.175R (N=2899) | +0.247R (N=4307) | +0.186R (N=7384) | +0.010R (N=1914) | +0.114R | +0.255R | +0.087R | +0.318R | NONE | NOT_CERTIFIED |
| PULLBACK | SIDEWAYS | 15796 | +0.208R (N=2086) | +0.138R (N=4185) | +0.107R (N=6196) | +0.015R (N=3329) | -0.031R | +0.210R | +0.206R | +0.036R | NONE | CERTIFIED_FOR_PRODUCTION |
| PULLBACK | BEAR | 11555 | +0.025R (N=2589) | +0.060R (N=2480) | +0.197R (N=3522) | -0.001R (N=2964) | -0.043R | +0.131R | +0.107R | +0.180R | CONCENTRATED_EDGE | NOT_CERTIFIED |
| ACCUMULATION | BULL | 6800 | +0.218R (N=1305) | +0.258R (N=1265) | +0.243R (N=3259) | -0.120R (N=971) | +0.250R | +0.171R | +0.115R | +0.301R | CONCENTRATED_EDGE | NOT_CERTIFIED |
| ACCUMULATION | SIDEWAYS | 4075 | +0.209R (N=590) | +0.011R (N=823) | +0.150R (N=1300) | +0.055R (N=1362) | -0.023R | +0.217R | +0.099R | +0.041R | NONE | NOT_CERTIFIED |
| ACCUMULATION | BEAR | 2775 | +0.040R (N=663) | +0.136R (N=673) | +0.206R (N=458) | +0.098R (N=981) | -0.060R | +0.219R | +0.203R | +0.096R | NONE | CERTIFIED_FOR_PRODUCTION |
| EOD | BULL | 907 | +0.316R (N=109) | +0.072R (N=206) | +0.144R (N=496) | +0.097R (N=96) | +0.241R | +0.184R | +0.003R | +0.214R | NONE | NOT_CERTIFIED |
| EOD | SIDEWAYS | 516 | +0.372R (N=55) | +0.161R (N=118) | +0.021R (N=189) | +0.065R (N=154) | -0.307R | +0.327R | +0.271R | +0.014R | NONE | NOT_CERTIFIED |
| EOD | BEAR | 339 | +0.075R (N=60) | +0.383R (N=61) | +0.005R (N=109) | +0.090R (N=109) | -0.105R | +0.400R | +0.242R | +0.100R | CONCENTRATED_EDGE | UNDER_CERTIFICATION |

---

## 2. In-Depth Multi-Cell Findings

### Scanner: TECHNICAL

#### Regime: BULL (N = 11,575)
- **End-to-End Holdout Passed:** `True` (Arm B CI low: +0.1114R, Delta CI low: +0.0186R, p: 0.00370)
- **Multi-Year Cells Breakdown:**
  * **Cell_1_2016_2018:** N=1,652 | Arm B Mean: +0.2197R (95% CI: [+0.1660, +0.2731]) | Delta: +0.0455R | Perm p: 0.0050 | Pass: `True`
  * **Cell_2_2019_2021:** N=3,472 | Arm B Mean: +0.2327R (95% CI: [+0.1956, +0.2701]) | Delta: +0.0974R | Perm p: 0.0001 | Pass: `True`
  * **Cell_3_2022_2024:** N=5,443 | Arm B Mean: +0.2206R (95% CI: [+0.1896, +0.2468]) | Delta: +0.0393R | Perm p: 0.0001 | Pass: `True`
  * **Cell_4_2025_2026:** N=1,008 | Arm B Mean: +0.1458R (95% CI: [+0.0792, +0.2126]) | Delta: +0.0797R | Perm p: 0.0001 | Pass: `True`
- **Quarterly Breakdown:**
  * **Q1:** N=2,210 | Arm B Mean: +0.2056R | Delta: +0.0841R | Pass: `True`
  * **Q2:** N=3,119 | Arm B Mean: +0.2959R | Delta: +0.0305R | Pass: `True`
  * **Q3:** N=3,835 | Arm B Mean: +0.1337R | Delta: +0.0793R | Pass: `True`
  * **Q4:** N=2,411 | Arm B Mean: +0.2605R | Delta: +0.0507R | Pass: `True`
- **Replication Consistency Flags:** `NONE`
- **Top Cell P&L Share:** 47.7%
- **Governance Verdict:** **`CERTIFIED_FOR_PRODUCTION`**

#### Regime: SIDEWAYS (N = 5,864)
- **End-to-End Holdout Passed:** `False` (Arm B CI low: -0.0214R, Delta CI low: -0.0147R, p: 0.07909)
- **Multi-Year Cells Breakdown:**
  * **Cell_1_2016_2018:** N=649 | Arm B Mean: +0.2139R (95% CI: [+0.1278, +0.3027]) | Delta: +0.1094R | Perm p: 0.0001 | Pass: `True`
  * **Cell_2_2019_2021:** N=1,455 | Arm B Mean: +0.2228R (95% CI: [+0.1664, +0.2794]) | Delta: +0.0062R | Perm p: 0.3780 | Pass: `False`
  * **Cell_3_2022_2024:** N=2,398 | Arm B Mean: +0.1373R (95% CI: [+0.0963, +0.1797]) | Delta: +0.0679R | Perm p: 0.0001 | Pass: `True`
  * **Cell_4_2025_2026:** N=1,362 | Arm B Mean: +0.0398R (95% CI: [-0.0178, +0.0988]) | Delta: +0.0423R | Perm p: 0.0120 | Pass: `False`
- **Quarterly Breakdown:**
  * **Q1:** N=849 | Arm B Mean: +0.0391R | Delta: +0.1428R | Pass: `False`
  * **Q2:** N=1,300 | Arm B Mean: +0.2633R | Delta: +0.0258R | Pass: `False`
  * **Q3:** N=1,705 | Arm B Mean: +0.2104R | Delta: +0.0210R | Pass: `False`
  * **Q4:** N=2,010 | Arm B Mean: +0.0559R | Delta: +0.0548R | Pass: `True`
- **Replication Consistency Flags:** `NONE`
- **Top Cell P&L Share:** 38.9%
- **Governance Verdict:** **`NOT_CERTIFIED`**

#### Regime: BEAR (N = 2,927)
- **End-to-End Holdout Passed:** `False` (Arm B CI low: +0.0134R, Delta CI low: -0.1079R, p: 1.00000)
- **Multi-Year Cells Breakdown:**
  * **Cell_1_2016_2018:** N=512 | Arm B Mean: -0.0203R (95% CI: [-0.1046, +0.0690]) | Delta: +0.1110R | Perm p: 0.0001 | Pass: `False`
  * **Cell_2_2019_2021:** N=628 | Arm B Mean: +0.3195R (95% CI: [+0.2298, +0.4126]) | Delta: +0.0735R | Perm p: 0.0030 | Pass: `True`
  * **Cell_3_2022_2024:** N=990 | Arm B Mean: +0.1331R (95% CI: [+0.0683, +0.2104]) | Delta: -0.0295R | Perm p: 1.0000 | Pass: `False`
  * **Cell_4_2025_2026:** N=797 | Arm B Mean: +0.0931R (95% CI: [+0.0245, +0.1716]) | Delta: +0.0063R | Perm p: 0.3840 | Pass: `False`
- **Quarterly Breakdown:**
  * **Q1:** N=986 | Arm B Mean: -0.0687R | Delta: +0.0648R | Pass: `False`
  * **Q2:** N=532 | Arm B Mean: +0.2839R | Delta: +0.0169R | Pass: `False`
  * **Q3:** N=680 | Arm B Mean: +0.3056R | Delta: -0.0025R | Pass: `False`
  * **Q4:** N=729 | Arm B Mean: +0.1443R | Delta: +0.0105R | Pass: `False`
- **Replication Consistency Flags:** `NONE`
- **Top Cell P&L Share:** 50.6%
- **Governance Verdict:** **`NOT_CERTIFIED`**

### Scanner: PULLBACK

#### Regime: BULL (N = 16,504)
- **End-to-End Holdout Passed:** `False` (Arm B CI low: -0.0743R, Delta CI low: -0.0167R, p: 0.06759)
- **Multi-Year Cells Breakdown:**
  * **Cell_1_2016_2018:** N=2,899 | Arm B Mean: +0.1751R (95% CI: [+0.1364, +0.2138]) | Delta: +0.0293R | Perm p: 0.0040 | Pass: `True`
  * **Cell_2_2019_2021:** N=4,307 | Arm B Mean: +0.2474R (95% CI: [+0.2176, +0.2783]) | Delta: +0.0039R | Perm p: 0.3250 | Pass: `False`
  * **Cell_3_2022_2024:** N=7,384 | Arm B Mean: +0.1859R (95% CI: [+0.1634, +0.2101]) | Delta: +0.0257R | Perm p: 0.0001 | Pass: `True`
  * **Cell_4_2025_2026:** N=1,914 | Arm B Mean: +0.0097R (95% CI: [-0.0382, +0.0524]) | Delta: +0.0432R | Perm p: 0.0020 | Pass: `False`
- **Quarterly Breakdown:**
  * **Q1:** N=3,142 | Arm B Mean: +0.1144R | Delta: +0.0432R | Pass: `True`
  * **Q2:** N=4,022 | Arm B Mean: +0.2545R | Delta: -0.0045R | Pass: `False`
  * **Q3:** N=6,022 | Arm B Mean: +0.0872R | Delta: +0.0491R | Pass: `True`
  * **Q4:** N=3,318 | Arm B Mean: +0.3182R | Delta: -0.0117R | Pass: `False`
- **Replication Consistency Flags:** `NONE`
- **Top Cell P&L Share:** 46.3%
- **Governance Verdict:** **`NOT_CERTIFIED`**

#### Regime: SIDEWAYS (N = 15,796)
- **End-to-End Holdout Passed:** `True` (Arm B CI low: +0.0429R, Delta CI low: +0.0286R, p: 0.00010)
- **Multi-Year Cells Breakdown:**
  * **Cell_1_2016_2018:** N=2,086 | Arm B Mean: +0.2082R (95% CI: [+0.1676, +0.2503]) | Delta: -0.0175R | Perm p: 1.0000 | Pass: `False`
  * **Cell_2_2019_2021:** N=4,185 | Arm B Mean: +0.1384R (95% CI: [+0.1095, +0.1687]) | Delta: +0.0027R | Perm p: 0.3960 | Pass: `False`
  * **Cell_3_2022_2024:** N=6,196 | Arm B Mean: +0.1069R (95% CI: [+0.0831, +0.1312]) | Delta: +0.0589R | Perm p: 0.0001 | Pass: `True`
  * **Cell_4_2025_2026:** N=3,329 | Arm B Mean: +0.0147R (95% CI: [-0.0170, +0.0496]) | Delta: +0.0703R | Perm p: 0.0001 | Pass: `False`
- **Quarterly Breakdown:**
  * **Q1:** N=2,439 | Arm B Mean: -0.0309R | Delta: +0.0492R | Pass: `False`
  * **Q2:** N=3,744 | Arm B Mean: +0.2099R | Delta: +0.0151R | Pass: `False`
  * **Q3:** N=3,932 | Arm B Mean: +0.2061R | Delta: +0.0244R | Pass: `True`
  * **Q4:** N=5,681 | Arm B Mean: +0.0359R | Delta: +0.0530R | Pass: `True`
- **Replication Consistency Flags:** `NONE`
- **Top Cell P&L Share:** 38.4%
- **Governance Verdict:** **`CERTIFIED_FOR_PRODUCTION`**

#### Regime: BEAR (N = 11,555)
- **End-to-End Holdout Passed:** `False` (Arm B CI low: -0.0379R, Delta CI low: +0.0211R, p: 0.00010)
- **Multi-Year Cells Breakdown:**
  * **Cell_1_2016_2018:** N=2,589 | Arm B Mean: +0.0249R (95% CI: [-0.0111, +0.0614]) | Delta: +0.0050R | Perm p: 0.2850 | Pass: `False`
  * **Cell_2_2019_2021:** N=2,480 | Arm B Mean: +0.0605R (95% CI: [+0.0206, +0.0996]) | Delta: +0.0043R | Perm p: 0.3320 | Pass: `False`
  * **Cell_3_2022_2024:** N=3,522 | Arm B Mean: +0.1966R (95% CI: [+0.1637, +0.2287]) | Delta: +0.0048R | Perm p: 0.3190 | Pass: `False`
  * **Cell_4_2025_2026:** N=2,964 | Arm B Mean: -0.0013R (95% CI: [-0.0387, +0.0334]) | Delta: +0.0643R | Perm p: 0.0001 | Pass: `False`
- **Quarterly Breakdown:**
  * **Q1:** N=4,055 | Arm B Mean: -0.0432R | Delta: +0.0371R | Pass: `False`
  * **Q2:** N=1,962 | Arm B Mean: +0.1309R | Delta: -0.0040R | Pass: `False`
  * **Q3:** N=2,417 | Arm B Mean: +0.1069R | Delta: +0.0149R | Pass: `False`
  * **Q4:** N=3,121 | Arm B Mean: +0.1804R | Delta: +0.0168R | Pass: `False`
- **Replication Consistency Flags:** `CONCENTRATED_EDGE`
- **Top Cell P&L Share:** 76.7%
- **Governance Verdict:** **`NOT_CERTIFIED`**

### Scanner: ACCUMULATION

#### Regime: BULL (N = 6,800)
- **End-to-End Holdout Passed:** `False` (Arm B CI low: -0.2762R, Delta CI low: -0.0294R, p: 0.11799)
- **Multi-Year Cells Breakdown:**
  * **Cell_1_2016_2018:** N=1,305 | Arm B Mean: +0.2178R (95% CI: [+0.1552, +0.2786]) | Delta: -0.0095R | Perm p: 1.0000 | Pass: `False`
  * **Cell_2_2019_2021:** N=1,265 | Arm B Mean: +0.2576R (95% CI: [+0.1998, +0.3192]) | Delta: -0.0199R | Perm p: 1.0000 | Pass: `False`
  * **Cell_3_2022_2024:** N=3,259 | Arm B Mean: +0.2428R (95% CI: [+0.2082, +0.2823]) | Delta: +0.0003R | Perm p: 0.5070 | Pass: `False`
  * **Cell_4_2025_2026:** N=971 | Arm B Mean: -0.1200R (95% CI: [-0.1860, -0.0541]) | Delta: +0.0352R | Perm p: 0.0400 | Pass: `False`
- **Quarterly Breakdown:**
  * **Q1:** N=1,213 | Arm B Mean: +0.2498R | Delta: +0.0202R | Pass: `False`
  * **Q2:** N=2,239 | Arm B Mean: +0.1710R | Delta: +0.0041R | Pass: `False`
  * **Q3:** N=2,198 | Arm B Mean: +0.1153R | Delta: +0.0164R | Pass: `False`
  * **Q4:** N=1,150 | Arm B Mean: +0.3006R | Delta: -0.0626R | Pass: `False`
- **Replication Consistency Flags:** `CONCENTRATED_EDGE`
- **Top Cell P&L Share:** 61.6%
- **Governance Verdict:** **`NOT_CERTIFIED`**

#### Regime: SIDEWAYS (N = 4,075)
- **End-to-End Holdout Passed:** `False` (Arm B CI low: -0.1283R, Delta CI low: +0.0500R, p: 0.00010)
- **Multi-Year Cells Breakdown:**
  * **Cell_1_2016_2018:** N=590 | Arm B Mean: +0.2093R (95% CI: [+0.1268, +0.2907]) | Delta: -0.0060R | Perm p: 1.0000 | Pass: `False`
  * **Cell_2_2019_2021:** N=823 | Arm B Mean: +0.0105R (95% CI: [-0.0628, +0.0828]) | Delta: +0.0446R | Perm p: 0.0290 | Pass: `False`
  * **Cell_3_2022_2024:** N=1,300 | Arm B Mean: +0.1503R (95% CI: [+0.1000, +0.2113]) | Delta: +0.0328R | Perm p: 0.0640 | Pass: `False`
  * **Cell_4_2025_2026:** N=1,362 | Arm B Mean: +0.0552R (95% CI: [-0.0018, +0.1119]) | Delta: +0.0808R | Perm p: 0.0001 | Pass: `False`
- **Quarterly Breakdown:**
  * **Q1:** N=663 | Arm B Mean: -0.0231R | Delta: +0.1003R | Pass: `False`
  * **Q2:** N=1,250 | Arm B Mean: +0.2170R | Delta: +0.0119R | Pass: `False`
  * **Q3:** N=1,010 | Arm B Mean: +0.0985R | Delta: +0.0072R | Pass: `False`
  * **Q4:** N=1,152 | Arm B Mean: +0.0411R | Delta: +0.0845R | Pass: `False`
- **Replication Consistency Flags:** `NONE`
- **Top Cell P&L Share:** 48.5%
- **Governance Verdict:** **`NOT_CERTIFIED`**

#### Regime: BEAR (N = 2,775)
- **End-to-End Holdout Passed:** `True` (Arm B CI low: +0.0743R, Delta CI low: +0.0064R, p: 0.00860)
- **Multi-Year Cells Breakdown:**
  * **Cell_1_2016_2018:** N=663 | Arm B Mean: +0.0402R (95% CI: [-0.0445, +0.1247]) | Delta: +0.0808R | Perm p: 0.0020 | Pass: `False`
  * **Cell_2_2019_2021:** N=673 | Arm B Mean: +0.1356R (95% CI: [+0.0509, +0.2203]) | Delta: +0.0100R | Perm p: 0.3600 | Pass: `False`
  * **Cell_3_2022_2024:** N=458 | Arm B Mean: +0.2065R (95% CI: [+0.1048, +0.3096]) | Delta: +0.0642R | Perm p: 0.0450 | Pass: `False`
  * **Cell_4_2025_2026:** N=981 | Arm B Mean: +0.0975R (95% CI: [+0.0327, +0.1641]) | Delta: +0.0603R | Perm p: 0.0080 | Pass: `True`
- **Quarterly Breakdown:**
  * **Q1:** N=770 | Arm B Mean: -0.0604R | Delta: +0.1386R | Pass: `False`
  * **Q2:** N=718 | Arm B Mean: +0.2194R | Delta: +0.0018R | Pass: `False`
  * **Q3:** N=689 | Arm B Mean: +0.2027R | Delta: -0.0128R | Pass: `False`
  * **Q4:** N=598 | Arm B Mean: +0.0961R | Delta: +0.0830R | Pass: `True`
- **Replication Consistency Flags:** `NONE`
- **Top Cell P&L Share:** 31.0%
- **Governance Verdict:** **`CERTIFIED_FOR_PRODUCTION`**

### Scanner: EOD

#### Regime: BULL (N = 907)
- **End-to-End Holdout Passed:** `False` (Arm B CI low: -0.2388R, Delta CI low: -0.0191R, p: 0.05939)
- **Multi-Year Cells Breakdown:**
  * **Cell_1_2016_2018:** N=109 | Arm B Mean: +0.3155R (95% CI: [+0.1043, +0.5411]) | Delta: +0.0965R | Perm p: 0.0690 | Pass: `False`
  * **Cell_2_2019_2021:** N=206 | Arm B Mean: +0.0721R (95% CI: [-0.0798, +0.2143]) | Delta: +0.1044R | Perm p: 0.0280 | Pass: `False`
  * **Cell_3_2022_2024:** N=496 | Arm B Mean: +0.1437R (95% CI: [+0.0507, +0.2409]) | Delta: +0.0415R | Perm p: 0.0770 | Pass: `False`
  * **Cell_4_2025_2026:** N=96 | Arm B Mean: +0.0967R (95% CI: [-0.1228, +0.3248]) | Delta: +0.2246R | Perm p: 0.0001 | Pass: `False`
- **Quarterly Breakdown:**
  * **Q1:** N=163 | Arm B Mean: +0.2413R | Delta: +0.0398R | Pass: `False`
  * **Q2:** N=293 | Arm B Mean: +0.1836R | Delta: +0.1223R | Pass: `True`
  * **Q3:** N=283 | Arm B Mean: +0.0027R | Delta: +0.0642R | Pass: `False`
  * **Q4:** N=168 | Arm B Mean: +0.2137R | Delta: +0.0815R | Pass: `False`
- **Replication Consistency Flags:** `NONE`
- **Top Cell P&L Share:** 54.9%
- **Governance Verdict:** **`NOT_CERTIFIED`**

#### Regime: SIDEWAYS (N = 516)
- **End-to-End Holdout Passed:** `False` (Arm B CI low: -0.1814R, Delta CI low: -0.0704R, p: 0.23538)
- **Multi-Year Cells Breakdown:**
  * **Cell_1_2016_2018:** N=55 | Arm B Mean: +0.3719R (95% CI: [+0.1081, +0.6294]) | Delta: +0.0449R | Perm p: 0.3030 | Pass: `False`
  * **Cell_2_2019_2021:** N=118 | Arm B Mean: +0.1605R (95% CI: [-0.0298, +0.3573]) | Delta: +0.0023R | Perm p: 0.4850 | Pass: `False`
  * **Cell_3_2022_2024:** N=189 | Arm B Mean: +0.0210R (95% CI: [-0.1195, +0.1604]) | Delta: +0.1581R | Perm p: 0.0001 | Pass: `False`
  * **Cell_4_2025_2026:** N=154 | Arm B Mean: +0.0648R (95% CI: [-0.0997, +0.2398]) | Delta: +0.0137R | Perm p: 0.3980 | Pass: `False`
- **Quarterly Breakdown:**
  * **Q1:** N=89 | Arm B Mean: -0.3075R | Delta: +0.1817R | Pass: `False`
  * **Q2:** N=110 | Arm B Mean: +0.3266R | Delta: -0.0262R | Pass: `False`
  * **Q3:** N=157 | Arm B Mean: +0.2712R | Delta: +0.0378R | Pass: `False`
  * **Q4:** N=160 | Arm B Mean: +0.0138R | Delta: +0.0969R | Pass: `False`
- **Replication Consistency Flags:** `NONE`
- **Top Cell P&L Share:** 38.4%
- **Governance Verdict:** **`NOT_CERTIFIED`**

#### Regime: BEAR (N = 339)
- **End-to-End Holdout Passed:** `False` (Arm B CI low: -0.1023R, Delta CI low: -0.2139R, p: 1.00000)
- **Multi-Year Cells Breakdown:**
  * **Cell_1_2016_2018:** N=60 | Arm B Mean: +0.0747R (95% CI: [-0.1716, +0.3540]) | Delta: +0.1539R | Perm p: 0.0500 | Pass: `False`
  * **Cell_2_2019_2021:** N=61 | Arm B Mean: +0.3833R (95% CI: [+0.1240, +0.6461]) | Delta: +0.0627R | Perm p: 0.2490 | Pass: `False`
  * **Cell_3_2022_2024:** N=109 | Arm B Mean: +0.0051R (95% CI: [-0.1852, +0.1903]) | Delta: -0.0772R | Perm p: 1.0000 | Pass: `False`
  * **Cell_4_2025_2026:** N=109 | Arm B Mean: +0.0905R (95% CI: [-0.1144, +0.3061]) | Delta: -0.0173R | Perm p: 1.0000 | Pass: `False`
- **Quarterly Breakdown:**
  * **Q1:** N=133 | Arm B Mean: -0.1052R | Delta: +0.0592R | Pass: `False`
  * **Q2:** N=75 | Arm B Mean: +0.4004R | Delta: -0.0440R | Pass: `False`
  * **Q3:** N=64 | Arm B Mean: +0.2425R | Delta: -0.0590R | Pass: `False`
  * **Q4:** N=67 | Arm B Mean: +0.1002R | Delta: +0.0293R | Pass: `False`
- **Replication Consistency Flags:** `CONCENTRATED_EDGE`
- **Top Cell P&L Share:** 61.1%
- **Governance Verdict:** **`UNDER_CERTIFICATION`**

---

## 3. Authoritative Production Routing Directive

Pursuant to the **Anti-Pooled-Bias Governance Charter**, only cells that pass data provenance, end-to-end holdout, AND cross-cell temporal replication are eligible for permanent production lock.

Any scanner awaiting additional temporal replication observations or exhibiting temporal inconsistency remains in **`UNDER_CERTIFICATION` (ZERO LIVE PRODUCTION ALERTS)**.
