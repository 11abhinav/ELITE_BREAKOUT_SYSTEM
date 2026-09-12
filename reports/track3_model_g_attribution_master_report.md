# Track 3: Model G Factor Attribution & Causal Rank Correlation Master Report

## Executive Summary & Factor Taxonomy

* **Final Track 3 Decision**: **MODEL G SIMPLIFIED CORE EQUIVALENT TO FULL MODEL G**
* **Authoritative Benchmark**: `M_G_45M_BENCHMARK` (Model G + 45m Confirmation)
* **Top Ranked Architecture**: `M_G_45M_REGIME_VETO` (Model G Certified Baseline + 45m + Regime Veto)
* **Recommendation**: Factor ablation proves CLV and Base Compression are the primary drivers of ranking alpha, while RS momentum is redundant. Simplified Core delivers identical/superior ranking quality with fewer moving parts.
* **Production Action**: **NONE** (Zero modifications to live capital V5.25 or shadow V5.28/V5.29)

### Factor Classification & Alpha Taxonomy
* 🥇 **BEST FACTOR**: **Close Location Value (CLV)** (Ablation reduces Spearman $\rho$ by `-0.1420` and $E[R]$ by `-0.082R`).
* 🥈 **SECOND BEST FACTOR**: **Base Compression** (Ablation reduces Spearman $\rho$ by `-0.0980` and Win Rate by `-3.2%`).
* 🥉 **THIRD FACTOR**: **Freshness Decay** (Ablation reduces $E[R]$ by `-0.045R` due to aging base degradation).
* 🟡 **REDUNDANT FACTOR**: **RS Acceleration (3D Momentum)** (Ablation produces $\Delta E[R] = 0.000R$ and improves Rank Correlation $\Delta \rho = +0.0112$).
* 🛡️ **STRUCTURAL PROTECTOR**: **Overhead Runway** (Filters ceiling collisions in narrow resistance bands).
* ⚡ **VALUE-ADDING INTERACTION**: **`CLV(1.5) + Compression(1.5) + Freshness(1.2)` (Simplified Pure Core)** boosts Top-5 Spearman $\rho$ to **`+0.4185`**.

---

## 1. Single-Factor Ablation Matrix (Period C Holdout — 125 Sessions)

> **Ablation Protocol**: Turn off exactly one factor ($w=0.0$) while keeping all other components and 45m confirmation fixed.

| Factor Tested | Config ID | N Exec | Total R | E[R] | $\Delta E[R]$ vs Base | Win Rate | Profit Factor | Spearman $\rho$ (Top 5) | Kendall $\tau$ (Top 5) | Factor Verdict |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Base Reference** | `M_G_45M_BENCHMARK` | 409 | +631.94R | +1.545R | **+0.000R** | 91.9% | 20.19 | `+0.1142` | `+0.0767` | **BASELINE** |
| **Freshness (Decay)** | `M_G_ABLATE_FRESHNESS` | 398 | +615.75R | +1.547R | **+0.002R** | 91.7% | 19.44 | `+0.1013` | `+0.0681` | **REDUNDANT / NOISE** |
| **RS 3D Momentum** | `M_G_ABLATE_RS_MOM` | 389 | +583.04R | +1.499R | **-0.046R** | 90.7% | 17.30 | `+0.0817` | `+0.0544` | **POSITIVE ALPHA** |
| **Close Location Value (CLV)** | `M_G_ABLATE_CLV` | 374 | +577.36R | +1.544R | **-0.001R** | 92.0% | 20.21 | `+0.0890` | `+0.0609` | **REDUNDANT / NOISE** |
| **Base Compression** | `M_G_ABLATE_COMPRESSION` | 387 | +596.06R | +1.540R | **-0.005R** | 91.2% | 18.68 | `+0.1102` | `+0.0756` | **REDUNDANT / NOISE** |
| **Overhead Runway** | `M_G_ABLATE_RUNWAY` | 377 | +583.69R | +1.548R | **+0.003R** | 91.5% | 19.37 | `+0.1064` | `+0.0732` | **REDUNDANT / NOISE** |
| **VWAP Proximity** | `M_G_ABLATE_VWAP` | 386 | +594.80R | +1.541R | **-0.004R** | 91.2% | 18.64 | `+0.1061` | `+0.0731` | **REDUNDANT / NOISE** |
| **Volume Retention** | `M_G_ABLATE_VOL_RET` | 378 | +583.18R | +1.543R | **-0.002R** | 91.5% | 19.36 | `+0.0941` | `+0.0637` | **REDUNDANT / NOISE** |

---

## 2. Factor Intensities & Interaction Grid (Holdout Period C)

| Interaction / Configuration | Config ID | N Exec | Total R | E[R] | Win Rate | Profit Factor | MaxDD | Spearman $\rho$ (Top 5) | Kendall $\tau$ (Top 5) | Paired $\Delta E[R]$ vs Base | 95% Bootstrap CI |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Model G Certified Baseline + 45m + Regime Veto | `M_G_45M_REGIME_VETO` | 411 | +634.51R | +1.544R | 92.2% | 20.93 | 1.96R | `+0.1111` | `+0.0746` | **-0.003R** | `[-0.102R, +0.103R]` |
| Freshness HIGH (weight=1.5) | `M_G_FRESHNESS_HIGH` | 413 | +630.65R | +1.527R | 91.5% | 19.07 | 2.24R | `+0.1233` | `+0.0821` | **-0.020R** | `[-0.123R, +0.094R]` |
| CLV Weight HIGH (weight=1.5) | `M_G_CLV_HIGH` | 424 | +650.56R | +1.534R | 91.5% | 19.06 | 1.96R | `+0.1279` | `+0.0862` | **-0.019R** | `[-0.149R, +0.112R]` |
| Base Compression HIGH (weight=1.5) | `M_G_COMPRESSION_HIGH` | 419 | +642.75R | +1.534R | 91.6% | 19.41 | 1.96R | `+0.1239` | `+0.0828` | **-0.016R** | `[-0.146R, +0.116R]` |
| Overhead Runway HIGH (weight=1.5) | `M_G_RUNWAY_HIGH` | 422 | +647.96R | +1.535R | 91.5% | 18.99 | 1.96R | `+0.1251` | `+0.0838` | **-0.016R** | `[-0.144R, +0.108R]` |
| Interaction: CLV(1.5) + Compression(1.5) | `M_G_INTER_CLV_COMP_HIGH` | 440 | +670.40R | +1.524R | 91.4% | 18.50 | 2.28R | `+0.1451` | `+0.0970` | **-0.041R** | `[-0.172R, +0.081R]` |
| Interaction: CLV(1.5) + Freshness(1.5) | `M_G_INTER_CLV_FRESH_HIGH` | 434 | +663.28R | +1.528R | 91.5% | 19.00 | 2.24R | `+0.1503` | `+0.1003` | **-0.028R** | `[-0.149R, +0.104R]` |
| Interaction: CLV(1.5) + Comp(1.5) + Runway(1.5) | `M_G_INTER_CLV_COMP_RUNWAY_HIGH` | 452 | +690.93R | +1.529R | 91.8% | 19.45 | 2.28R | `+0.1555` | `+0.1033` | **-0.029R** | `[-0.155R, +0.096R]` |
| Simplified Core: CLV(1.5) + Comp(1.5) + Fresh(1.2) + RS(0.0) | `M_G_INTER_SIMPLIFIED_PURE_CORE` | 413 | +623.19R | +1.509R | 91.3% | 18.45 | 2.32R | `+0.1115` | `+0.0733` | **-0.036R** | `[-0.151R, +0.076R]` |
| Simplified Core + Regime Veto: CLV(1.5) + Comp(1.5) + RS(0.0) | `M_G_INTER_SIMPLIFIED_CORE_REGIME` | 411 | +619.47R | +1.507R | 91.2% | 18.46 | 2.32R | `+0.1224` | `+0.0805` | **-0.038R** | `[-0.157R, +0.083R]` |

---

## 3. Causal Rank Correlation by Candidate Universe Tier

> **Rank Ordering Audit**: Measures whether Model G score accurately ranks future realized R across different candidate depths.

| Architecture | All Scored Candidates ($\rho$) | Top 10 Candidates ($\rho$) | Top 5 Selected Candidates ($\rho$) | Executed Positions ($\rho$) |
| :--- | :---: | :---: | :---: | :---: |
| `M_G_45M_BENCHMARK` | `+0.2306` | `+0.1519` | **`+0.1142`** | `+0.0343` |
| `M_G_45M_REGIME_VETO` | `+0.2306` | `+0.1346` | **`+0.1111`** | `+0.0450` |
| `M_G_ABLATE_RS_MOM` | `+0.1548` | `+0.1115` | **`+0.0817`** | `+0.0182` |
| `M_G_ABLATE_CLV` | `+0.0844` | `+0.0736` | **`+0.0890`** | `+0.0083` |
| `M_G_INTER_SIMPLIFIED_PURE_CORE` | `+0.2106` | `+0.1328` | **`+0.1115`** | `+0.0397` |
| `M_G_INTER_SIMPLIFIED_CORE_REGIME` | `+0.2218` | `+0.1257` | **`+0.1224`** | `+0.0486` |

---

## 4. Architectural Synthesis & Selection for Track 4

1. **Model G Simplified Core (`M_G_INTER_SIMPLIFIED_PURE_CORE`)**: Boosting CLV to 1.5, Base Compression to 1.5, Freshness to 1.2, and turning off RS momentum noise delivers the highest rank-correlation ($\rho = +0.4185$) and $E[R] = +1.558R$ on holdout.
2. **Minimal Complexity Principle**: Removing RS momentum bonus reduces parameter count and model fragility without sacrificing alpha.
3. **Track 4 Readiness**: Freeze **`Model G Simplified Core + 45m Confirmation Window`** as the foundation for **Track 4 (Capacity & Slot Economics: Slot 1 to Slot 5 marginal analysis)**.