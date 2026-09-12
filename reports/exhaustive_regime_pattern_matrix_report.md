# EXHAUSTIVE COMBINATORIAL PATTERN x REGIME MATRIX REPORT

**Execution Timestamp**: 2026-09-12T20:17:19+05:30 (IST)  
**Universe Audited**: 871 Real BSE/NSE Equities  
**Execution Duration**: 154.09 seconds  

---

## 1. Optimal Regime-to-Pattern Policy Matrix

| Market Regime | Primary Champion Pattern | Secondary Confluence Pattern | Top Expectancy ($E[R]$) | Top Profit Factor | Prohibited / Blocked Patterns |
| :--- | :--- | :--- | :---: | :---: | :--- |
| **`STRONG_BULL`** | **`MULTI_MONTH_BASE_BREAKOUT`** | `WYCKOFF_2 + EMA_BOUNCE` | **+0.5219R** | **2.129** | `DB_SHAKEOUT + WYCKOFF_2`, `ASC_TRIANGLE + BULL_FLAG` |
| **`SIDEWAYS`** | **`BULL_FLAG`** | `ASCENDING_TRIANGLE` | **+0.3277R** | **1.720** | `VCP_CONTRACTION`, `INVERSE_HEAD_AND_SHOULDERS`, `UNDERCUT_AND_RALLY`, `FALLING_WEDGE_REVERSAL` |
| **`BULL`** | **`BULL_FLAG`** | `WYCKOFF_SPRING_TYPE_2` | **+0.1939R** | **1.361** | `INVERSE_HEAD_AND_SHOULDERS`, `PULLBACK_EMA_BOUNCE`, `PENNANT_CONVERGENCE`, `FLAT_BASE + BULL_FLAG` |
| **`HIGH_VOLATILITY`** | **`DB_SHAKEOUT + WYCKOFF_2`** | `WYCKOFF_2 + EMA_BOUNCE` | **+0.8972R** | **4.091** | `VCP_CONTRACTION`, `ASC_TRIANGLE + BULL_FLAG` |
| **`WEAK_BEAR`** | **`VCP_CONTRACTION`** | `NONE` | **+0.0000R** | **0.000** | `WYCKOFF_SPRING_TYPE_2`, `FLAT_BASE_BREAKOUT`, `MULTI_MONTH_BASE_BREAKOUT`, `DOUBLE_BOTTOM_SHAKEOUT` |

---

## 2. Regime-by-Regime Forensic Leaderboards

### Regime: `W1_BULL_RALLY` (STRONG_BULL — Broad Market Bull Expansion)

| Rank | Pattern / Confluence Entity | Trades ($N$) | Expectancy ($E[R]$) | Profit Factor | Win Rate | Bootstrap 95% CI |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **#1** | `BULL_FLAG + EMA_BOUNCE` | 7 | **+1.5441R** | **9.990** | 100.0% | `[+0.860, +2.176]` |
| **#2** | `DB_SHAKEOUT + WYCKOFF_2` | 24 | **+0.8551R** | **3.379** | 62.5% | `[+0.246, +1.426]` |
| **#3** | `FLAT_BASE + BULL_FLAG` | 53 | **+0.8059R** | **3.542** | 66.0% | `[+0.431, +1.145]` |
| **#4** | `BULL_FLAG + MULTI_MONTH_BASE` | 28 | **+0.7494R** | **3.317** | 64.3% | `[+0.225, +1.234]` |
| **#5** | `BULL_FLAG` | 526 | **+0.6414R** | **2.724** | 59.9% | `[+0.523, +0.757]` |
| **#6** | `CUP_AND_HANDLE` | 32 | **+0.6261R** | **2.759** | 62.5% | `[+0.163, +1.099]` |
| **#7** | `WYCKOFF_2 + EMA_BOUNCE` | 109 | **+0.5355R** | **2.463** | 56.9% | `[+0.295, +0.788]` |
| **#8** | `PENNANT_CONVERGENCE` | 17 | **+0.5175R** | **2.100** | 52.9% | `[-0.175, +1.212]` |

### Regime: `W2_SIDEWAYS_CHOP` (SIDEWAYS — Consolidation & Rangebound Chop)

| Rank | Pattern / Confluence Entity | Trades ($N$) | Expectancy ($E[R]$) | Profit Factor | Win Rate | Bootstrap 95% CI |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **#1** | `ASC_TRIANGLE + BULL_FLAG` | 7 | **+1.1737R** | **5.108** | 71.4% | `[+0.286, +2.062]` |
| **#2** | `BULL_FLAG` | 332 | **+0.3277R** | **1.720** | 51.2% | `[+0.187, +0.475]` |
| **#3** | `ASCENDING_TRIANGLE` | 85 | **+0.2970R** | **1.609** | 48.2% | `[+0.006, +0.592]` |
| **#4** | `FLAT_BASE_BREAKOUT` | 257 | **+0.2784R** | **1.587** | 48.2% | `[+0.116, +0.446]` |
| **#5** | `WYCKOFF_SPRING_TYPE_2` | 815 | **+0.2488R** | **1.502** | 46.6% | `[+0.156, +0.345]` |
| **#6** | `MULTI_MONTH_BASE_BREAKOUT` | 108 | **+0.2179R** | **1.446** | 48.1% | `[-0.031, +0.469]` |
| **#7** | `FLAT_BASE + BULL_FLAG` | 38 | **+0.2142R** | **1.460** | 47.4% | `[-0.208, +0.646]` |
| **#8** | `PULLBACK_EMA_BOUNCE` | 514 | **+0.0982R** | **1.176** | 40.5% | `[-0.021, +0.212]` |

### Regime: `W3_MIDCAP_RALLY` (BULL — Institutional Midcap Expansion)

| Rank | Pattern / Confluence Entity | Trades ($N$) | Expectancy ($E[R]$) | Profit Factor | Win Rate | Bootstrap 95% CI |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **#1** | `BULL_FLAG` | 523 | **+0.1939R** | **1.361** | 43.8% | `[+0.073, +0.316]` |
| **#2** | `WYCKOFF_SPRING_TYPE_2` | 1172 | **+0.1888R** | **1.364** | 44.0% | `[+0.113, +0.265]` |
| **#3** | `FLAT_BASE_BREAKOUT` | 349 | **+0.1877R** | **1.344** | 41.3% | `[+0.053, +0.332]` |
| **#4** | `BULL_FLAG + MULTI_MONTH_BASE` | 39 | **+0.1847R** | **1.334** | 41.0% | `[-0.248, +0.624]` |
| **#5** | `UNDERCUT_AND_RALLY` | 863 | **+0.1308R** | **1.233** | 40.1% | `[+0.035, +0.224]` |
| **#6** | `ASCENDING_TRIANGLE` | 97 | **+0.1135R** | **1.196** | 40.2% | `[-0.162, +0.376]` |
| **#7** | `MULTI_MONTH_BASE_BREAKOUT` | 175 | **+0.0504R** | **1.083** | 37.1% | `[-0.144, +0.254]` |
| **#8** | `DOUBLE_BOTTOM_SHAKEOUT` | 418 | **+0.0228R** | **1.038** | 36.4% | `[-0.107, +0.152]` |

### Regime: `W4_EVENT_VOLATILITY` (HIGH_VOLATILITY — Event & High VIX Spikes)

| Rank | Pattern / Confluence Entity | Trades ($N$) | Expectancy ($E[R]$) | Profit Factor | Win Rate | Bootstrap 95% CI |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **#1** | `DB_SHAKEOUT + WYCKOFF_2` | 11 | **+0.8972R** | **4.091** | 63.6% | `[+0.076, +1.748]` |
| **#2** | `WYCKOFF_2 + EMA_BOUNCE` | 102 | **+0.6950R** | **3.103** | 61.8% | `[+0.427, +0.949]` |
| **#3** | `DOUBLE_BOTTOM_SHAKEOUT` | 167 | **+0.5716R** | **2.496** | 57.5% | `[+0.355, +0.783]` |
| **#4** | `WYCKOFF_SPRING_TYPE_2` | 868 | **+0.5423R** | **2.373** | 56.7% | `[+0.447, +0.638]` |
| **#5** | `BULL_FLAG + EMA_BOUNCE` | 23 | **+0.5262R** | **2.484** | 56.5% | `[-0.034, +1.109]` |
| **#6** | `PENNANT_CONVERGENCE` | 68 | **+0.4932R** | **2.158** | 54.4% | `[+0.152, +0.829]` |
| **#7** | `UNDERCUT_AND_RALLY` | 413 | **+0.4757R** | **2.204** | 55.2% | `[+0.348, +0.602]` |
| **#8** | `FALLING_WEDGE_REVERSAL` | 167 | **+0.4683R** | **2.221** | 56.3% | `[+0.264, +0.679]` |

### Regime: `W5_ATH_MOMENTUM` (STRONG_BULL — Summer All-Time High Momentum)

| Rank | Pattern / Confluence Entity | Trades ($N$) | Expectancy ($E[R]$) | Profit Factor | Win Rate | Bootstrap 95% CI |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **#1** | `MULTI_MONTH_BASE_BREAKOUT` | 148 | **+0.5219R** | **2.129** | 51.4% | `[+0.277, +0.769]` |
| **#2** | `WYCKOFF_2 + EMA_BOUNCE` | 122 | **+0.4087R** | **1.931** | 49.2% | `[+0.163, +0.662]` |
| **#3** | `BULL_FLAG + MULTI_MONTH_BASE` | 40 | **+0.3589R** | **1.704** | 45.0% | `[-0.114, +0.831]` |
| **#4** | `UNDERCUT_AND_RALLY` | 468 | **+0.3434R** | **1.731** | 48.5% | `[+0.212, +0.480]` |
| **#5** | `WYCKOFF_SPRING_TYPE_2` | 806 | **+0.3391R** | **1.732** | 48.8% | `[+0.244, +0.441]` |
| **#6** | `FLAT_BASE_BREAKOUT` | 299 | **+0.3312R** | **1.665** | 47.8% | `[+0.176, +0.495]` |
| **#7** | `PULLBACK_EMA_BOUNCE` | 572 | **+0.3057R** | **1.627** | 45.5% | `[+0.189, +0.427]` |
| **#8** | `FLAT_BASE + BULL_FLAG` | 35 | **+0.3054R** | **1.616** | 48.6% | `[-0.172, +0.773]` |

### Regime: `W6_CORRECTION` (WEAK_BEAR — Correction & Defensive Rotation)

| Rank | Pattern / Confluence Entity | Trades ($N$) | Expectancy ($E[R]$) | Profit Factor | Win Rate | Bootstrap 95% CI |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **#1** | `VCP_CONTRACTION` | 6 | **+0.8257R** | **3.421** | 50.0% | `[-0.341, +2.159]` |
| **#2** | `FALLING_WEDGE_REVERSAL` | 7 | **+0.2845R** | **1.498** | 42.9% | `[-1.000, +1.622]` |
| **#3** | `BULL_FLAG` | 13 | **+0.0484R** | **1.070** | 30.8% | `[-0.763, +0.937]` |
| **#4** | `UNDERCUT_AND_RALLY` | 15 | **+0.0313R** | **1.056** | 33.3% | `[-0.630, +0.792]` |
| **#5** | `WYCKOFF_SPRING_TYPE_2` | 36 | **-0.0551R** | **0.919** | 30.6% | `[-0.503, +0.471]` |
| **#6** | `FLAT_BASE_BREAKOUT` | 13 | **-0.2520R** | **0.672** | 23.1% | `[-1.000, +0.517]` |
| **#7** | `MULTI_MONTH_BASE_BREAKOUT` | 5 | **-0.3456R** | **0.568** | 20.0% | `[-1.000, +0.963]` |
| **#8** | `DOUBLE_BOTTOM_SHAKEOUT` | 13 | **-0.6753R** | **0.188** | 7.7% | `[-1.000, -0.190]` |


---

## 3. Dynamic Policy Implementation Guidelines

1. **STRONG_BULL / ATH Momentum**: Deploy `BULL_FLAG` (+$15$ pts) and `MULTI_MONTH_BASE_BREAKOUT` (+$10$ pts). All momentum breakouts enjoy unrestricted flow.
2. **HIGH_VOLATILITY / Event VIX**: Deploy `WYCKOFF_SPRING_TYPE_2` (+$15$ pts) and `UNDERCUT_AND_RALLY` (+$10$ pts). High volatility stop-sweeps provide maximum asymmetry ($>+0.50R$).
3. **SIDEWAYS / Rangebound Chop**: Deploy `BULL_FLAG` and `ASCENDING_TRIANGLE` (higher lows prevent false base breakdowns).
4. **WEAK_BEAR / Market Correction**: **Block all standard base breakouts** (`FLAT_BASE`, `ASCENDING_TRIANGLE`, `MULTI_MONTH_BASE`). Restrict live triggers strictly to `FALLING_WEDGE_REVERSAL` and `UNDERCUT_AND_RALLY`.
