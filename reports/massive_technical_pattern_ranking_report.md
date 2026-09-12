# FORENSIC MASTER TECHNICAL PATTERN TOURNAMENT & RANKING REPORT

**Execution Timestamp**: 2026-09-12T18:55:23+05:30 (IST)  
**Universe Audited**: 871 Real BSE/NSE Equities (`data/history/1d/*.parquet`)  
**Total Pattern Trades Simulated**: 19,027 Causal Trades  
**Evaluation Scope**: 14 Mathematical Patterns across 6 Real Market Regime Slices  
**Execution Duration**: 106.68 seconds  

---

## 1. Executive Master Leaderboard (Sorted by CMPR Score)

| Rank | Pattern Name | Tier Allocation | CMPR Score | Expectancy ($E[R]$) | Profit Factor | Win Rate | Max DD ($R$) | Bootstrap 95% CI | Sample ($N$) |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **#1** | `WYCKOFF_SPRING_TYPE_2` | **TIER_1_CORE_ALPHA** | **83.2** | **+0.3399R** | **1.730** | 49.3% | 21.8R | `[+0.295, +0.383]` | 4,596 |
| **#2** | `BULL_FLAG` | **TIER_1_CORE_ALPHA** | **83.1** | **+0.3254R** | **1.672** | 48.5% | 30.8R | `[+0.270, +0.375]` | 2,581 |
| **#3** | `MULTI_MONTH_BASE_BREAKOUT` | **TIER_1_CORE_ALPHA** | **78.3** | **+0.3013R** | **1.603** | 46.7% | 10.8R | `[+0.182, +0.412]` | 662 |
| **#4** | `FLAT_BASE_BREAKOUT` | **TIER_1_CORE_ALPHA** | **78.2** | **+0.3068R** | **1.626** | 47.2% | 21.7R | `[+0.237, +0.377]` | 1,498 |
| **#5** | `ASCENDING_TRIANGLE` | **TIER_1_CORE_ALPHA** | **71.2** | **+0.2503R** | **1.489** | 44.6% | 15.0R | `[+0.133, +0.371]` | 502 |
| **#6** | `UNDERCUT_AND_RALLY` | **TIER_2_CONFLUENCE_BONUS** | **67.9** | **+0.2257R** | **1.440** | 44.3% | 16.9R | `[+0.171, +0.280]` | 2,649 |
| **#7** | `PULLBACK_EMA_BOUNCE` | **TIER_2_CONFLUENCE_BONUS** | **64.3** | **+0.2299R** | **1.446** | 44.3% | 24.2R | `[+0.182, +0.282]` | 2,846 |
| **#8** | `FALLING_WEDGE_REVERSAL` | **TIER_3_OBSERVATIONAL_TAG** | **54.0** | **+0.1506R** | **1.277** | 41.7% | 25.4R | `[+0.051, +0.244]` | 748 |
| **#9** | `DOUBLE_BOTTOM_SHAKEOUT` | **TIER_3_OBSERVATIONAL_TAG** | **45.0** | **+0.0973R** | **1.171** | 39.5% | 49.2R | `[+0.023, +0.178]` | 1,208 |
| **#10** | `INVERSE_HEAD_AND_SHOULDERS` | **TIER_3_OBSERVATIONAL_TAG** | **44.2** | **+0.1373R** | **1.250** | 41.3% | 33.7R | `[-0.005, +0.289]` | 378 |
| **#11** | `PENNANT_CONVERGENCE` | **TIER_3_OBSERVATIONAL_TAG** | **41.3** | **+0.1140R** | **1.196** | 39.6% | 16.9R | `[-0.071, +0.299]` | 227 |
| **#12** | `VCP_CONTRACTION` | **TIER_4_RESEARCH_REJECTED** | **36.0** | **+0.0574R** | **1.095** | 35.7% | 17.0R | `[-0.063, +0.187]` | 476 |
| **#13** | `HIGH_TIGHT_FLAG` | **TIER_4_RESEARCH_REJECTED** | **26.2** | **+0.0680R** | **1.113** | 40.0% | 4.0R | `[-0.600, +0.806]` | 15 |
| **#14** | `CUP_AND_HANDLE` | **TIER_4_RESEARCH_REJECTED** | **25.2** | **+0.0229R** | **1.037** | 35.3% | 84.1R | `[-0.091, +0.131]` | 641 |

---

## 2. Multi-Regime Breakdown Across 6 Real Historical Market Windows

| Pattern Name | W1 (2025 Q3 Bull) | W2 (2025 Q4 Chop) | W3 (2026 Q1 Midcap) | W4 (2026 Q2 Event) | W5 (2026 Q3 ATH) | W6 (2026 Monsoon) | Positive Regimes |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `WYCKOFF_SPRING_TYPE_2` | **+0.44R** (899) | **+0.25R** (815) | **+0.19R** (1172) | **+0.54R** (868) | **+0.34R** (806) | -0.06R (36) | **5 / 6 (83.3%)** |
| `BULL_FLAG` | **+0.64R** (526) | **+0.33R** (332) | **+0.19R** (523) | **+0.25R** (702) | **+0.23R** (485) | **+0.05R** (13) | **6 / 6 (100.0%)** |
| `MULTI_MONTH_BASE_BREAKOUT` | **+0.45R** (109) | **+0.22R** (108) | **+0.05R** (175) | **+0.37R** (117) | **+0.52R** (148) | -0.35R (5) | **5 / 6 (83.3%)** |
| `FLAT_BASE_BREAKOUT` | **+0.46R** (371) | **+0.28R** (257) | **+0.19R** (349) | **+0.27R** (209) | **+0.33R** (299) | -0.25R (13) | **5 / 6 (83.3%)** |
| `ASCENDING_TRIANGLE` | **+0.39R** (94) | **+0.30R** (85) | **+0.11R** (97) | **+0.31R** (110) | **+0.19R** (113) | -1.00R (3) | **5 / 6 (83.3%)** |
| `UNDERCUT_AND_RALLY` | **+0.40R** (416) | -0.08R (474) | **+0.13R** (863) | **+0.48R** (413) | **+0.34R** (468) | **+0.03R** (15) | **5 / 6 (83.3%)** |
| `PULLBACK_EMA_BOUNCE` | **+0.46R** (511) | **+0.10R** (514) | -0.03R (711) | **+0.44R** (519) | **+0.31R** (572) | -0.76R (19) | **4 / 6 (66.7%)** |
| `FALLING_WEDGE_REVERSAL` | **+0.45R** (127) | -0.14R (134) | -0.13R (185) | **+0.47R** (167) | **+0.14R** (128) | **+0.28R** (7) | **4 / 6 (66.7%)** |
| `DOUBLE_BOTTOM_SHAKEOUT` | **+0.33R** (176) | -0.32R (234) | **+0.02R** (418) | **+0.57R** (167) | **+0.19R** (200) | -0.68R (13) | **4 / 6 (66.7%)** |
| `INVERSE_HEAD_AND_SHOULDERS` | **+0.39R** (64) | -0.01R (43) | -0.00R (79) | **+0.15R** (130) | **+0.10R** (60) | +0.00R (2) | **3 / 6 (50.0%)** |
| `PENNANT_CONVERGENCE` | **+0.52R** (17) | -0.69R (32) | -0.05R (55) | **+0.49R** (68) | **+0.18R** (54) | +0.00R (1) | **3 / 6 (50.0%)** |
| `VCP_CONTRACTION` | **+0.51R** (62) | -0.00R (82) | -0.31R (76) | -0.17R (121) | **+0.27R** (129) | **+0.83R** (6) | **3 / 6 (50.0%)** |
| `HIGH_TIGHT_FLAG` | +0.00R (0) | +0.00R (1) | +0.00R (2) | **+0.45R** (9) | +0.00R (3) | +0.00R (0) | **1 / 6 (16.7%)** |
| `CUP_AND_HANDLE` | **+0.63R** (32) | -0.52R (64) | -0.42R (114) | **+0.18R** (284) | **+0.19R** (143) | -1.00R (4) | **3 / 6 (50.0%)** |

---

## 3. Key Quantitative Insights & Governance Tiers

### 🌟 New Major Discovery: `WYCKOFF_SPRING_TYPE_2` (#1 Overall)
- **Mathematical Definition**: Primary undercut flush followed by a secondary retest at $[t-3, t]$ that holds above the spring low on drying volume ($<0.85\times$ Spring Vol), followed by an expansion reclaim candle.
- **Empirical Edge**: **$+0.3399R$ Expectancy, 1.730 Profit Factor, 49.3% Win Rate across 4,596 trades**. Positive in 5 out of 6 regimes.
- **Strategic Fit**: Represents a high-conviction structural confirmation upgrade for Reversal and Wealth bottoming candidates.

### 🚀 Momentum Winner: `BULL_FLAG` (#2 Overall)
- **Empirical Edge**: **$+0.3254R$ Expectancy, 1.672 Profit Factor, 48.5% Win Rate across 2,581 trades**.
- **Regime Invariance**: **Positive in 6 / 6 regimes (100.0%)**. Highly durable continuation engine across all phases.

### 🏛️ Base Breakouts: `MULTI_MONTH_BASE_BREAKOUT` (#3 Overall)
- **Empirical Edge**: **$+0.3013R$ Expectancy, 1.603 Profit Factor, lowest drawdown at only 10.8R**.
- **Strategic Fit**: Ideal for Stage-2 EOD Breakout enhancement.

### ⚠️ Textbook Patterns that Fail in Indian Equities (Tier 4 Quarantined)
- **`CUP_AND_HANDLE`**: Net $+0.0229R$, Profit Factor 1.037, Max Drawdown $84.1R$. Fails severely in sideways and choppy markets (W2: $-0.52R$, W3: $-0.42R$).
- **`VCP_CONTRACTION` (Standalone)**: Net $+0.0574R$, Profit Factor 1.095. Too fragile without fundamental RS/volume filters.
- **`HIGH_TIGHT_FLAG`**: Only 15 historical occurrences in 1 year; sample size too small for standalone trigger.
