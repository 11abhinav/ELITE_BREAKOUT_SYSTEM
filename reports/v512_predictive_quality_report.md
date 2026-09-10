# V5.12 Predictive Entry Quality Report

**Date:** 2026-09-10  |  **Dual Control:** V5.8 + V5.11  |  **Fresh Forward:** 100% PRISTINE

## 1. Three-Way Comparison (V5.8 vs V5.11 vs V5.12)

| Scanner | V5.8 WR | V5.11 WR | V5.12 WR | ΔWR | V5.8 E[R] | V5.11 E[R] | V5.12 E[R] | ΔE[R] | PF | Feature | BE |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :---: |
| **ACCUMULATION_VCP** | 40.76% | 40.76% | **44.26%** | **+3.50%** | -0.053R | +0.021R | **+0.118R** | **+0.0970R** | 1.309 | `F2F4F6_RS_VOL_BRD` | +0.5R |
| **EOD_BREAKOUT** | 41.01% | 41.01% | **44.92%** | **+3.91%** | -0.074R | +0.005R | **+0.150R** | **+0.1447R** | 1.382 | `F1F9_CPOS_EARLY` | +0.5R |
| **SHORT_COVERING** | 34.67% | 34.67% | **37.45%** | **+2.78%** | -0.068R | +0.040R | **+0.151R** | **+0.1107R** | 1.269 | `F1F3F12_CPOS_SEC_MOM` | +0.5R |
| **MULTITF_1H** | 36.58% | 36.58% | **48.96%** | **+12.38%** | -0.028R | +0.081R | **+0.455R** | **+0.3740R** | 2.073 | `F1F2F4_CPOS_RS_VOL` | +0.5R |

## 2. Single Feature Attribution (Top 20 by ΔWR)

| Scanner | Feature | N | WR | ΔWR | E[R] | ΔE[R] | CI |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| ACCUMULATION_VCP | `F6_BREADTH60` | 9594 | 43.57% | **+2.81%** | +0.0107R | -0.0103R | [-0.012,+0.034] |
| ACCUMULATION_VCP | `F6_BREADTH60` | 9594 | 43.57% | **+2.81%** | +0.0993R | +0.0783R | [+0.077,+0.121] |
| ACCUMULATION_VCP | `F6_BREADTH60` | 9594 | 43.57% | **+2.81%** | +0.0887R | +0.0677R | [+0.067,+0.111] |
| ACCUMULATION_VCP | `F6_BREADTH60` | 9594 | 43.57% | **+2.81%** | +0.0825R | +0.0615R | [+0.060,+0.105] |
| ACCUMULATION_VCP | `F6_BREADTH60` | 9594 | 43.57% | **+2.81%** | +0.0610R | +0.0400R | [+0.038,+0.084] |
| EOD_BREAKOUT | `F8_CLEANGAP` | 4275 | 43.56% | **+2.55%** | -0.0008R | -0.0058R | [-0.037,+0.036] |
| EOD_BREAKOUT | `F8_CLEANGAP` | 4275 | 43.56% | **+2.55%** | +0.0981R | +0.0931R | [+0.063,+0.133] |
| EOD_BREAKOUT | `F8_CLEANGAP` | 4275 | 43.56% | **+2.55%** | +0.0845R | +0.0795R | [+0.050,+0.119] |
| EOD_BREAKOUT | `F8_CLEANGAP` | 4275 | 43.56% | **+2.55%** | +0.0749R | +0.0699R | [+0.040,+0.110] |
| EOD_BREAKOUT | `F8_CLEANGAP` | 4275 | 43.56% | **+2.55%** | +0.0544R | +0.0494R | [+0.019,+0.090] |
| SHORT_COVERING | `F1_CPOS80` | 458 | 36.90% | **+2.23%** | +0.0324R | -0.0076R | [-0.109,+0.174] |
| SHORT_COVERING | `F1_CPOS80` | 458 | 36.90% | **+2.23%** | +0.1646R | +0.1246R | [+0.029,+0.300] |
| SHORT_COVERING | `F1_CPOS80` | 458 | 36.90% | **+2.23%** | +0.1132R | +0.0732R | [-0.025,+0.251] |
| SHORT_COVERING | `F1_CPOS80` | 458 | 36.90% | **+2.23%** | +0.1392R | +0.0992R | [+0.002,+0.276] |
| SHORT_COVERING | `F1_CPOS80` | 458 | 36.90% | **+2.23%** | +0.1518R | +0.1118R | [+0.015,+0.288] |
| EOD_BREAKOUT | `F6_BREADTH60` | 7958 | 42.89% | **+1.88%** | +0.0252R | +0.0202R | [-0.001,+0.051] |
| EOD_BREAKOUT | `F6_BREADTH60` | 7958 | 42.89% | **+1.88%** | +0.0542R | +0.0492R | [+0.029,+0.080] |
| EOD_BREAKOUT | `F6_BREADTH60` | 7958 | 42.89% | **+1.88%** | +0.0444R | +0.0394R | [+0.019,+0.070] |
| EOD_BREAKOUT | `F6_BREADTH60` | 7958 | 42.89% | **+1.88%** | -0.0314R | -0.0364R | [-0.058,-0.005] |
| EOD_BREAKOUT | `F6_BREADTH60` | 7958 | 42.89% | **+1.88%** | +0.0679R | +0.0629R | [+0.043,+0.093] |

## 3. Interaction Discovery — Top 20

| Scanner | Combo | Type | BE | N | WR | ΔWR | E[R] | ΔE[R] | CI |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| MULTITF_1H | `F1F2F4_CPOS_RS_VOL` | TRIPLE | +0.0R | 96 | **48.96%** | **+12.38%** | +0.3404R | +0.2594R | [+0.023,+0.658] |
| MULTITF_1H | `F1F2F4_CPOS_RS_VOL` | TRIPLE | +1.5R | 96 | **48.96%** | **+12.38%** | +0.4029R | +0.3219R | [+0.093,+0.712] |
| MULTITF_1H | `F1F2F4_CPOS_RS_VOL` | TRIPLE | +1.0R | 96 | **48.96%** | **+12.38%** | +0.4237R | +0.3427R | [+0.117,+0.730] |
| MULTITF_1H | `F1F2F4_CPOS_RS_VOL` | TRIPLE | +0.5R | 96 | **48.96%** | **+12.38%** | +0.4550R | +0.3740R | [+0.153,+0.757] |
| MULTITF_1H | `F1F4_CPOS_VOL15` | PAIR | +1.5R | 299 | **41.47%** | **+4.89%** | +0.1698R | +0.0888R | [+0.008,+0.332] |
| MULTITF_1H | `F1F4F12_CPOS_VOL_MOM` | TRIPLE | +0.0R | 299 | **41.47%** | **+4.89%** | +0.0962R | +0.0152R | [-0.070,+0.262] |
| MULTITF_1H | `F1F4F12_CPOS_VOL_MOM` | TRIPLE | +0.5R | 299 | **41.47%** | **+4.89%** | +0.2267R | +0.1457R | [+0.069,+0.385] |
| MULTITF_1H | `F1F4F12_CPOS_VOL_MOM` | TRIPLE | +1.0R | 299 | **41.47%** | **+4.89%** | +0.1966R | +0.1156R | [+0.036,+0.357] |
| MULTITF_1H | `F1F4F12_CPOS_VOL_MOM` | TRIPLE | +1.5R | 299 | **41.47%** | **+4.89%** | +0.1698R | +0.0888R | [+0.008,+0.332] |
| MULTITF_1H | `F1F4_CPOS_VOL15` | PAIR | +1.0R | 299 | **41.47%** | **+4.89%** | +0.1966R | +0.1156R | [+0.036,+0.357] |
| MULTITF_1H | `F1F4_CPOS_VOL15` | PAIR | +0.0R | 299 | **41.47%** | **+4.89%** | +0.0962R | +0.0152R | [-0.070,+0.262] |
| MULTITF_1H | `F1F4_CPOS_VOL15` | PAIR | +0.5R | 299 | **41.47%** | **+4.89%** | +0.2267R | +0.1457R | [+0.069,+0.385] |
| MULTITF_1H | `F1F3F4_CPOS_SEC_VOL` | TRIPLE | +1.5R | 179 | **41.34%** | **+4.76%** | +0.1549R | +0.0739R | [-0.052,+0.362] |
| MULTITF_1H | `F1F3F4_CPOS_SEC_VOL` | TRIPLE | +1.0R | 179 | **41.34%** | **+4.76%** | +0.1801R | +0.0991R | [-0.025,+0.386] |
| MULTITF_1H | `F1F3F4_CPOS_SEC_VOL` | TRIPLE | +0.5R | 179 | **41.34%** | **+4.76%** | +0.2137R | +0.1327R | [+0.011,+0.416] |
| MULTITF_1H | `F1F3F4_CPOS_SEC_VOL` | TRIPLE | +0.0R | 179 | **41.34%** | **+4.76%** | +0.0823R | +0.0013R | [-0.131,+0.295] |
| MULTITF_1H | `F1F2F3_CPOS_RS_SEC` | TRIPLE | +1.5R | 121 | **40.50%** | **+3.92%** | +0.2479R | +0.1669R | [-0.025,+0.520] |
| MULTITF_1H | `F1F2F3_CPOS_RS_SEC` | TRIPLE | +1.0R | 121 | **40.50%** | **+3.92%** | +0.2727R | +0.1917R | [+0.003,+0.543] |
| MULTITF_1H | `F1F2F3_CPOS_RS_SEC` | TRIPLE | +0.5R | 121 | **40.50%** | **+3.92%** | +0.3057R | +0.2247R | [+0.039,+0.572] |
| MULTITF_1H | `F1F2F3_CPOS_RS_SEC` | TRIPLE | +0.0R | 121 | **40.50%** | **+3.92%** | +0.1735R | +0.0925R | [-0.106,+0.453] |

## 4. Regime Matrix

| Scanner | Variant | Regime | N | WR | E[R] | PF |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| ACCUMULATION_VCP | `ACCUMULATION_VCP_V512_F2F4F6_RS_VOL_BRD_BE5` | **BULL** | 1245 | 44.26% | **+0.1180R** | 1.309 |
| ACCUMULATION_VCP | `ACCUMULATION_VCP_V512_F2F4F6_RS_VOL_BRD_BE5` | **NEUTRAL** | 0 | 0.00% | **-9.9900R** | 0.000 |
| ACCUMULATION_VCP | `ACCUMULATION_VCP_V512_F2F4F6_RS_VOL_BRD_BE5` | **BEAR** | 0 | 0.00% | **-9.9900R** | 0.000 |
| EOD_BREAKOUT | `EOD_BREAKOUT_V512_F1F9_CPOS_EARLY_BE5` | **BULL** | 359 | 44.29% | **+0.1502R** | 1.378 |
| EOD_BREAKOUT | `EOD_BREAKOUT_V512_F1F9_CPOS_EARLY_BE5` | **NEUTRAL** | 112 | 47.32% | **+0.1595R** | 1.433 |
| EOD_BREAKOUT | `EOD_BREAKOUT_V512_F1F9_CPOS_EARLY_BE5` | **BEAR** | 1 | 0.00% | **-9.9900R** | 0.000 |
| EOD_BREAKOUT | `EOD_BREAKOUT_V512_F1F4F7_CPOS_VOL_COMP_BE5` | **BULL** | 181 | 46.96% | **+0.2013R** | 1.544 |
| EOD_BREAKOUT | `EOD_BREAKOUT_V512_F1F4F7_CPOS_VOL_COMP_BE5` | **NEUTRAL** | 68 | 39.71% | **+0.1256R** | 1.365 |
| EOD_BREAKOUT | `EOD_BREAKOUT_V512_F1F4F7_CPOS_VOL_COMP_BE5` | **BEAR** | 2 | 0.00% | **-9.9900R** | 0.000 |
| SHORT_COVERING | `SHORT_COVERING_V512_F1_CPOS80_BE5` | **BULL** | 2 | 0.00% | **-9.9900R** | 0.000 |
| SHORT_COVERING | `SHORT_COVERING_V512_F1_CPOS80_BE5` | **NEUTRAL** | 75 | 41.33% | **+0.1664R** | 1.314 |
| SHORT_COVERING | `SHORT_COVERING_V512_F1_CPOS80_BE5` | **BEAR** | 381 | 35.96% | **+0.1631R** | 1.290 |
| SHORT_COVERING | `SHORT_COVERING_V512_F1F3F12_CPOS_SEC_MOM_BE5` | **BULL** | 0 | 0.00% | **-9.9900R** | 0.000 |
| SHORT_COVERING | `SHORT_COVERING_V512_F1F3F12_CPOS_SEC_MOM_BE5` | **NEUTRAL** | 47 | 46.81% | **+0.3022R** | 1.688 |
| SHORT_COVERING | `SHORT_COVERING_V512_F1F3F12_CPOS_SEC_MOM_BE5` | **BEAR** | 212 | 35.38% | **+0.1171R** | 1.199 |
| MULTITF_1H | `MULTITF_1H_V512_F1F2F4_CPOS_RS_VOL_BE5` | **BULL** | 80 | 50.00% | **+0.5016R** | 2.263 |
| MULTITF_1H | `MULTITF_1H_V512_F1F2F4_CPOS_RS_VOL_BE5` | **NEUTRAL** | 14 | 50.00% | **+0.4115R** | 1.855 |
| MULTITF_1H | `MULTITF_1H_V512_F1F2F4_CPOS_RS_VOL_BE5` | **BEAR** | 2 | 0.00% | **-9.9900R** | 0.000 |

## 5. Key Findings

- **4/4 scanners** improved WR vs V5.11
- **0 scanners** retained V5.11 champion
- Best WR gain: **MULTITF_1H** ΔWR=+12.38% via `F1F2F4_CPOS_RS_VOL`

---
*V5.12 Research | Fresh Forward 100% PRISTINE | Dual Control: V5.8 + V5.11*
