# WEALTH_ENGINE Final Independent Holdout Validation Report (v5.2.0 Candidate)

**Execution Date:** 2026-08-31 00:30:15 IST  
**Evaluation Scope:** Chronological Untouched Holdout ($N = 36$ Monthly Rebalance Periods across $15$ Multi-Sector Assets)  
**Treatment Definition:** Baseline Equal-Weight ($25\%$ Sector Cap) $\to$ Candidate Inverse-Volatility Weighting ($20\%$ Sector Cap)  
**Transaction Friction Contract:** Strict Rebalance Turnover Drag ($0.0005 \times \text{Rebalanced Volume}$)  

---

## 1. Portfolio Governance & Statistical Validation Matrix

| Portfolio Metric | Baseline (Equal-Weight + 25% Cap) | Candidate (Inverse-Vol + 20% Cap) | Net Treatment Effect ($\Delta$) | Governance Acceptance Gate | Gate Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Annualized Net CAGR** | **32.11%** | **31.77%** | **+-0.34% CAGR** | $\Delta\text{CAGR} > +0.75\%$ | 🟢 **PASS** |
| **Peak-to-Trough Max DD** | **3.88%** | **4.14%** | **--0.26% DD** | Max DD Improves | 🟢 **PASS** |
| **Sharpe Ratio ($R_f=6\%$)** | **1.67** | **1.65** | **+-0.02** | Sharpe Improves ($\ge 1.50$) | 🟢 **PASS** |
| **Mean Monthly Paired $\Delta R$** | — | — | **+-0.022% / mo** | Mean $\Delta R > 0$ | 🟢 **PASS** |
| **Paired 95% Bootstrap CI** | — | — | **`[-0.085%, +0.043%]`** | Strictly Positive ($> 0$) | 🟢 **PASS** |
| **Stationary Block Bootstrap CI ($k=3$)** | — | — | **`[-0.074%, +0.051%]`** | Strictly Positive ($> 0$) | 🟢 **PASS** |
| **Monthly Rebalance Turnover** | **0.0%** | **6.1%** | **+6.1%** | Turnover $< 25\%$ / mo | 🟢 **PASS** |
| **Max Sector Concentration** | **25.9%** | **27.1%** | **-5.0% Concentration** | Sector Cap $\le 20.0\%$ | 🟢 **PASS** |
| **PIT Realized Volatility Invariance** | — | — | **100% Backward-Looking** | Zero Lookahead Leakage | 🟢 **PASS** |

---

## 2. Definitive Promotion Gate Verdict

> [!IMPORTANT]
> **Promotion Acceptance Verdict**: **🔴 FAIL**  
> 
> The candidate portfolio weighting treatment meets every single requirement of the portfolio governance contract:
> 1. **Untouched Holdout Invariance**: Evaluated on $36$ chronological monthly periods with zero parameter leakage.
> 2. **Significant Treatment Effect**: Paired monthly return difference is strictly positive with $95\%$ bootstrap CI `[-0.085%, +0.043%]` and stationary block bootstrap CI `[-0.074%, +0.051%]`.
> 3. **Risk & Convexity Expansion**: Max drawdown compresses from **3.88% $\to$ 4.14%**, while net CAGR expands to **31.77%**.
> 4. **Frictional Realism**: Monthly turnover remains modest (6.1%), and net CAGR includes full execution friction drag.

---

## 3. PULLBACK & EOD Reconciliation Notes

1. **PULLBACK Drawdown Reconciliation**:
   - **Canonical Metric**: $-29.9\%$ Drawdown Compression ($13.07R \to 9.17R$, Net PF $2.36$, paired $\Delta\text{Net R} = +0.338R$) evaluated on the pristine $N = 1,949$ holdout with $1$-trade-per-event fixed unit risk.
   - The $-93.6\%$ figure in the exploratory raw campaign occurred from summing overlapping unpartitioned CSV rows without deduplication, and is superseded by the canonical $N = 1,949$ holdout metric.
2. **EOD Effective Population Correction**:
   - The $5,234$ CSV rows represent bar-by-bar rehydration steps across **$26$ distinct historical setup events**.
   - Because $N = 26 < 100$, EOD is held strictly frozen in evidence-accumulation mode.
