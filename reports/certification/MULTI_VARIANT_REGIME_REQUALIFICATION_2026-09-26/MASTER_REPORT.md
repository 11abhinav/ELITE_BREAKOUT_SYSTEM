# MASTER MULTI-VARIANT × MULTI-REGIME REQUALIFICATION REPORT

**Audit Date:** 2026-09-26 | **Governing Evidence Release:** `TEMPORAL_REPLICATION_2026-09-26`
**Total Tested Combinations:** 36 (3 Scanners × 4 Variants × 3 Regimes)

## 1. Summary of Retention Decisions

| Scanner Family | Decision | Qualifying Variants | Discarded Variants | Production Status |
| :--- | :---: | :---: | :---: | :--- |
| **ACCUMULATION** | `DISCARD_SCANNER_FAMILY` | 0 | 4 | **DISCARDED FROM PRODUCTION** |
| **PULLBACK** | `DISCARD_SCANNER_FAMILY` | 0 | 4 | **DISCARDED FROM PRODUCTION** |
| **EOD** | `DISCARD_SCANNER_FAMILY` | 0 | 4 | **DISCARDED FROM PRODUCTION** |

---

## 2. Granular Variant × Regime Matrix

| Scanner | Variant ID | Variant Name | Regime | N | Mean Net R [95% CI] | Holdout CI | Top Cell Share | Alpha p | Verdict | Primary Failure Mode |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **ACCUMULATION** | `ACC-V01-BASE` | Base VCP Contraction | **BULL** | 6,800 | +0.1890R [+0.164, +0.215] | [-0.231, -0.059] | 56.5% | 1.0000 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **ACCUMULATION** | `ACC-V01-BASE` | Base VCP Contraction | **SIDEWAYS** | 4,075 | +0.0988R [+0.067, +0.131] | [-0.080, +0.060] | 48.5% | 1.0000 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **ACCUMULATION** | `ACC-V01-BASE` | Base VCP Contraction | **BEAR** | 2,775 | +0.1110R [+0.071, +0.150] | [+0.117, +0.306] | 31.0% | 1.0000 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **ACCUMULATION** | `ACC-V02-VOL-SURGE` | Volume Surge Confirmed | **BULL** | 333 | +0.2914R [+0.161, +0.420] | [-0.426, +0.511] | 55.1% | 0.1107 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **ACCUMULATION** | `ACC-V02-VOL-SURGE` | Volume Surge Confirmed | **SIDEWAYS** | 172 | +0.1348R [-0.034, +0.292] | [-0.336, +0.338] | 69.7% | 0.6661 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **ACCUMULATION** | `ACC-V02-VOL-SURGE` | Volume Surge Confirmed | **BEAR** | 131 | +0.1435R [-0.029, +0.346] | [-0.333, +0.627] | 63.5% | 0.7431 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **ACCUMULATION** | `ACC-V03-TREND-STRENGTH` | Macro Trend Aligned | **BULL** | 4,321 | +0.2228R [+0.189, +0.257] | [-0.157, +0.097] | 54.3% | 0.1187 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **ACCUMULATION** | `ACC-V03-TREND-STRENGTH` | Macro Trend Aligned | **SIDEWAYS** | 2,443 | +0.0970R [+0.056, +0.142] | [+0.008, +0.196] | 46.6% | 0.9482 | **`FAIL`** | Incremental alpha failed vs Base (p=0.9482, d=-0.002) |
| **ACCUMULATION** | `ACC-V03-TREND-STRENGTH` | Macro Trend Aligned | **BEAR** | 1,176 | +0.1536R [+0.088, +0.216] | [+0.043, +0.304] | 33.1% | 0.2662 | **`FAIL`** | Incremental alpha failed vs Base (p=0.2662, d=0.039) |
| **ACCUMULATION** | `ACC-V04-VOLATILITY-ADAPTIVE` | Strict Volatility Squeeze | **BULL** | 2,367 | +0.1882R [+0.143, +0.234] | [-0.340, -0.004] | 71.9% | 0.9770 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **ACCUMULATION** | `ACC-V04-VOLATILITY-ADAPTIVE` | Strict Volatility Squeeze | **SIDEWAYS** | 1,302 | +0.0803R [+0.025, +0.138] | [-0.143, +0.096] | 67.9% | 0.5935 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **ACCUMULATION** | `ACC-V04-VOLATILITY-ADAPTIVE` | Strict Volatility Squeeze | **BEAR** | 740 | +0.0948R [+0.011, +0.176] | [-0.077, +0.280] | 48.2% | 0.7256 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **PULLBACK** | `PB-V01-BASE` | Base Continuation | **BULL** | 16,504 | +0.1796R [+0.164, +0.195] | [-0.049, +0.077] | 46.3% | 1.0000 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **PULLBACK** | `PB-V01-BASE` | Base Continuation | **SIDEWAYS** | 15,796 | +0.1092R [+0.094, +0.124] | [+0.058, +0.136] | 38.4% | 1.0000 | **`FAIL`** | Modern 2025-26 decay (modern CI crosses zero) |
| **PULLBACK** | `PB-V01-BASE` | Base Continuation | **BEAR** | 11,555 | +0.0782R [+0.061, +0.096] | [-0.015, +0.083] | 76.4% | 1.0000 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **PULLBACK** | `PB-V02-DEEP-SUPPORT` | Confluence Support Bounce | **BULL** | 5,768 | +0.1811R [+0.156, +0.208] | [-0.054, +0.147] | 43.7% | 0.9233 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **PULLBACK** | `PB-V02-DEEP-SUPPORT` | Confluence Support Bounce | **SIDEWAYS** | 5,778 | +0.0916R [+0.066, +0.117] | [+0.027, +0.163] | 37.3% | 0.2507 | **`FAIL`** | Incremental alpha failed vs Base (p=0.2507, d=-0.018) |
| **PULLBACK** | `PB-V02-DEEP-SUPPORT` | Confluence Support Bounce | **BEAR** | 4,183 | +0.0517R [+0.020, +0.081] | [-0.016, +0.142] | 88.2% | 0.1349 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **PULLBACK** | `PB-V03-RSI-OVERSOLD` | Momentum Dip-Buyer | **BULL** | 6,034 | +0.1930R [+0.167, +0.218] | [-0.069, +0.126] | 43.7% | 0.3887 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **PULLBACK** | `PB-V03-RSI-OVERSOLD` | Momentum Dip-Buyer | **SIDEWAYS** | 5,939 | +0.1130R [+0.089, +0.139] | [+0.045, +0.171] | 41.3% | 0.7979 | **`FAIL`** | Incremental alpha failed vs Base (p=0.7979, d=0.004) |
| **PULLBACK** | `PB-V03-RSI-OVERSOLD` | Momentum Dip-Buyer | **BEAR** | 4,540 | +0.0839R [+0.055, +0.112] | [-0.046, +0.121] | 82.7% | 0.7393 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **PULLBACK** | `PB-V04-STRUCTURAL-PIVOT` | Prior Resistance Reclaim | **BULL** | 9,060 | +0.1719R [+0.152, +0.193] | [-0.011, +0.153] | 43.4% | 0.5653 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **PULLBACK** | `PB-V04-STRUCTURAL-PIVOT` | Prior Resistance Reclaim | **SIDEWAYS** | 9,220 | +0.0939R [+0.074, +0.115] | [+0.032, +0.129] | 39.2% | 0.2380 | **`FAIL`** | Incremental alpha failed vs Base (p=0.2380, d=-0.015) |
| **PULLBACK** | `PB-V04-STRUCTURAL-PIVOT` | Prior Resistance Reclaim | **BEAR** | 6,357 | +0.0671R [+0.044, +0.091] | [-0.048, +0.087] | 70.5% | 0.4675 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **EOD** | `EOD-V01-BASE` | Base 20D Breakout | **BULL** | 907 | +0.1431R [+0.070, +0.214] | [-0.260, +0.301] | 54.9% | 1.0000 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **EOD** | `EOD-V01-BASE` | Base 20D Breakout | **SIDEWAYS** | 516 | +0.1034R [+0.016, +0.196] | [-0.227, +0.186] | 38.3% | 1.0000 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **EOD** | `EOD-V01-BASE` | Base 20D Breakout | **BEAR** | 339 | +0.1129R [+0.003, +0.223] | [-0.166, +0.408] | 61.1% | 1.0000 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **EOD** | `EOD-V02-52W-MOMENTUM` | 52-Week High Proximity | **BULL** | 300 | +0.1096R [-0.013, +0.232] | [-0.440, +0.683] | 62.8% | 0.6406 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **EOD** | `EOD-V02-52W-MOMENTUM` | 52-Week High Proximity | **SIDEWAYS** | 129 | +0.0945R [-0.079, +0.278] | [-0.296, +0.608] | 69.3% | 0.9295 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **EOD** | `EOD-V02-52W-MOMENTUM` | 52-Week High Proximity | **BEAR** | 83 | +0.1598R [-0.055, +0.374] | [-0.295, +0.751] | 36.1% | 0.7026 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **EOD** | `EOD-V03-COMPRESSION-EXPANSION` | Volatility Squeeze Breakout | **BULL** | 130 | +0.0975R [-0.084, +0.287] | [-0.717, +0.837] | 77.7% | 0.6596 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **EOD** | `EOD-V03-COMPRESSION-EXPANSION` | Volatility Squeeze Breakout | **SIDEWAYS** | 67 | -0.0956R [-0.342, +0.161] | [-0.723, +0.449] | 80.2% | 0.1585 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **EOD** | `EOD-V03-COMPRESSION-EXPANSION` | Volatility Squeeze Breakout | **BEAR** | 32 | +0.2857R [-0.071, +0.618] | [-1.019, +0.981] | 74.1% | 0.3738 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **EOD** | `EOD-V04-MOMENTUM-CORRIDOR` | RSI Corridor & CLV | **BULL** | 217 | +0.1507R [+0.006, +0.292] | [-0.551, +0.883] | 46.0% | 0.9271 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **EOD** | `EOD-V04-MOMENTUM-CORRIDOR` | RSI Corridor & CLV | **SIDEWAYS** | 115 | +0.1107R [-0.076, +0.310] | [-0.535, +0.213] | 51.9% | 0.9461 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
| **EOD** | `EOD-V04-MOMENTUM-CORRIDOR` | RSI Corridor & CLV | **BEAR** | 91 | +0.0227R [-0.183, +0.231] | [-0.618, +0.287] | 52.3% | 0.4600 | **`FAIL`** | Holdout failed (CI_low <= 0 or p >= 0.05) |
