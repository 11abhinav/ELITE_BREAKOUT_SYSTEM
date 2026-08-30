# Wave 4 — Alert Quality Model (`AQS_EOD_v1`) Validation Report

**Report Generated:** 2026-08-30 19:36:00 IST  
**Model ID:** `AQS_EOD_v1` (Regularized Ridge Linear, $\lambda=10.0$)  
**Target Metric:** Net Realized Trade R ($y = \text{Gross } R - 0.05\text{R}$ friction)  
**Operating Mode:** Alert Ranking / Prioritization  
**Dataset Hash:** `86e71bacec2fe08a53615e0083b007b1c1e376e72a797da47d1161c2934713d0`  

---

## 1. Executive Summary & Governance Verdict

| Model ID | Scanner Scope | Operating Mode | Holdout $N$ | Baseline Population Net $E[R]$ | Top-Ranked Net $E[R]$ | Incremental $\Delta E[R]$ (BCa 95% CI) | Trade Retention | Three-Tier Scoring (Mech / Stat / Econ) | Governance Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **`AQS_EOD_v1`** | `EOD` | **Ranking / Prioritization** | 18 | **+0.46R** | **+1.10R** | **+0.64R** (`+0.00R` to `+0.64R`) | **100.0% (Pure Ranking)** | `PASS` / `INCONCLUSIVE` / `PROMISING` | **`PROMISING_RANKING_SIGNAL (NOT_PRODUCTION_VALIDATED)`** |

---

## 2. Reconciled Holdout Accounting & Arithmetic

Direct inspection of the 18 holdout records:

| Symbol | Records | Entry / Target / SL | `label_A_t1_hit` (Binary Target) | Gross Realized R | Net Realized R (-0.05R Friction) | AQS Score | AQS Ranking Priority |
|---|---|---|---|---|---|---|---|
| **`MCX`** | 5 | ₹3040.00 / **₹3040.00** / ₹2898.67 | `True` *(entry==target)* | **+0.00R** | **-0.05R** (Friction drag) | `52.38` | Priority 2 (Lower) |
| **`BALUFORGE`** | 5 | ₹542.55 / **₹542.55** / ₹512.51 | `True` *(entry==target)* | **+0.00R** | **-0.05R** (Friction drag) | `50.04` | Priority 3 (Lower) |
| **`RELIANCE`** | 8 | ₹1254.80 / ₹1378.00 / ₹1214.00 | `False` *(Target not reached)* | **+1.15R** | **+1.10R** (Net Gain) | `76.34` | **Priority 1 (Top)** |

### Correct Arithmetic Reconciliation:
$$\begin{aligned}
\text{Total Holdout Net R Sum:} & \quad 8(+1.10\text{R}) + 5(-0.05\text{R}) + 5(-0.05\text{R}) = 8.80 - 0.25 - 0.25 = \mathbf{+8.30\text{R}} \\
\text{Baseline Population Mean Net } E[R]: & \quad \frac{+8.30\text{R}}{18} = \mathbf{+0.461\text{R}} \\
\text{Top-Ranked Subset Mean Net } E[R]: & \quad \frac{+8.80\text{R}}{8} = \mathbf{+1.100\text{R}} \\
\text{Incremental Ranking Delta } \Delta E[R]: & \quad +1.100\text{R} - (+0.461\text{R}) = \mathbf{+0.639\text{R} \approx +0.64\text{R}}
\end{aligned}$$

- **Key Takeaway:** AQS successfully prioritized the economically profitable alerts (`RELIANCE` at $+1.10\text{R}$ net) over the zero-gain / friction-generating mock setups (`MCX`, `BALUFORGE` at $-0.05\text{R}$ net).
- **Symbol Concentration Caveat:** Because $N=18$ spans only 3 securities, this result is treated as a **promising ranking hypothesis**, not production-grade proof.

---

## 3. Forward Evidence Protocol ($N \ge 50$ New Independent Alerts)

To achieve production promotion, forward telemetry in read-only shadow mode will evaluate:

1. **Rank Monotonicity Across Buckets:**
   $$E[\text{Net } R]_{\text{Top 20\%}} > E[\text{Net } R]_{\text{Mid 60\%}} > E[\text{Net } R]_{\text{Bottom 20\%}}$$
2. **Spearman Rank Correlation:** $\rho(\text{AQS}, \text{Net } R) > 0$.
3. **Multi-Metric Attribution:** Win rate by AQS quintile, MAE/MFE by AQS bucket, and portfolio Max Drawdown.
4. **Pure Ranking Operation:** $100\%$ opportunity retention (no hard rejection gates).

---

## 4. Production Promotion Status

> [!IMPORTANT]
> **Production Promotion Strictly Locked:**
> - Live production scanner logic (`EOD`, `Multi-TF`, `Reversal`) remains **100% untouched**.
> - `AQS_EOD_v1` is deployed in **Read-Only Shadow Mode as an Alert Ranking / Prioritization Score**.
> - Forward shadow data accumulation will track $N \ge 50$ genuinely new live production alerts before any promotion review.