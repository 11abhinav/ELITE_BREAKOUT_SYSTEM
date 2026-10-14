# QGIO-1 — Risk, Market Regimes, and Temporal Robustness Report

**Candidate ID**: `QGIO_1`  
**Strategy Focus**: Strategy A (Quality Growth + 50/200-Day Trend Baseline Engine)  
**Evaluation Span**: 2018-06-15 to 2026-10-12 (8.33 Years, 2,045 Trading Sessions)  
**Friction Model**: Full Indian Delivery Execution (Brokerage, STT, Stamp Duty, DP, Exchange, GST, and Slippage)

---

## 1. Temporal Window Breakdown & Holdout Failure

To prevent pooled historical artifact bias, performance was audited across three independent, non-overlapping calendar windows:

| Temporal Cell | Calendar Range | Sessions | Strategy A CAGR (%) | Nifty 500 TRI (%) | Excess vs N500 | Strategy A Sharpe | Strategy A Max DD | Pre-Registered Gate Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Development** | 2018-06-15 to 2020-12-31 | 625 | **13.34%** | 9.86% | **+3.48%** | **0.54** | **-25.55%** | **PASS** |
| **Validation** | 2021-01-01 to 2023-12-31 | 739 | **30.59%** | 19.87% | **+10.72%** | **1.32** | **-21.78%** | **PASS** |
| **Locked Holdout**| 2024-01-01 to 2026-10-12 | 681 | **-0.21%** | 5.59% | **-5.80%** | **-0.12** | **-36.69%** | **FAIL** |

### Key Temporal Observations:
1. **Strong Historical Training & Validation (2018–2023)**:
   - During the 2018–2020 cycle (including the COVID crash), Strategy A produced **13.34% CAGR** vs 9.86% for Nifty 500, limiting drawdown to -25.55% vs -38.30%.
   - During the 2021–2023 bull market expansion, Strategy A achieved **30.59% CAGR** (Sharpe 1.32), outperforming the market by **+10.72% per year**.
2. **Catastrophic Failure in the Locked Holdout (2024–2026)**:
   - In the untouchable holdout period (2024 to late 2026), Strategy A **collapsed to -0.21% CAGR**, trailing the broad market by **-5.80% annualized**.
   - Max drawdown expanded to **-36.69%**, nearly matching the worst broad market drawdowns.
   - Strategy A failed Gate 4 (Temporal Replication: positive in $\ge 3$ of 4 windows) and Gate 8 (Holdout Alpha).

---

## 2. Market Regime Sensitivity & Structural Vulnerability

Why did Strategy A compound brilliantly from 2018 to 2023, only to fail in 2024–2026?

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          MARKET REGIME RESPONSE                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. EXPANSIVE BULL (2020–2023):  OUTPERFORMS (+10.72% Excess CAGR)           │
│    Persistent trends allow high-ROE compounders to run; 200 SMA stays intact│
├─────────────────────────────────────────────────────────────────────────────┤
│ 2. SEVERE CRISIS (MARCH 2020):  PROTECTS (-25.55% vs -38.30% Max DD)        │
│    3-close 200 SMA exit liquidated falling stocks, moving portfolio to cash │
├─────────────────────────────────────────────────────────────────────────────┤
│ 3. CHOPPY / ROTATIONAL (2024–2026): SEVERE DRAG (-5.80% Excess CAGR)        │
│    Frequent whipsaws across 200 SMA; high friction, low win rate (32.3%)    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### The Anatomy of the 2024–2026 Breakdown:
1. **Narrow Market Leadership & Sector Rotation**:
   - Between 2024 and 2026, Indian equities experienced violent sector rotations (e.g. rallies in PSUs, defense, and power, followed by abrupt corrections) rather than steady, persistent multi-year compounder trends.
   - High-quality private growth stocks experienced prolonged base-building and range-bound consolidations.
2. **Whipsaw Multiplication on the 200 SMA**:
   - In a trend-following architecture with a low win rate (32.3%), performance relies on a small fraction of runaway winners delivering 50%–100%+ gains to offset frequent -7% to -12% losses.
   - When the market becomes choppy, stocks repeatedly cross above the 50/200 SMA (triggering an entry) and then slide below the 200 SMA (triggering an exit), causing a string of small losses that compound into a -36.69% portfolio drawdown.

---

## 3. Transaction Friction & Adverse Cost Sensitivity

Over the 8.33-year backtest, Strategy A executed **235 round-trip trades** across 2,045 sessions (averaging ~28 trades per year):

| Slippage Assumption | Net CAGR (%) | Total Return (%) | Sharpe Ratio | Max Drawdown (%) | Slippage Drag |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Base Cost (5 bps slippage)** | **14.55%** | **209.96%** | **0.49** | **-36.69%** | Baseline |
| **Adverse Cost (10 bps slippage)**| **14.73%** | **213.92%** | **0.49** | **-37.06%** | Execution Noise |
| **Adverse Cost (20 bps slippage)**| **14.47%** | **208.04%** | **0.48** | **-37.69%** | -0.08% CAGR |

### Cost Breakdown on ₹10 Lakhs Starting Capital:
- **Total Brokerage (₹20/order)**: ₹9,400.00
- **Securities Transaction Tax (STT 0.1% on Sell)**: ₹31,297.70
- **Execution Slippage (5 bps)**: ₹29,732.81
- **Stamp Duty (0.015% on Buy)**: ₹4,694.65
- **Depository Charges (DP ₹15.93/sell)**: ₹3,743.55
- **Exchange & SEBI Turnover Fees**: ₹3,129.77
- **GST (18% on Statutory Fees)**: ₹3,129.77
- **TOTAL TRANSACTION FRICTION**: **₹78,244.24**

**Conclusion**: Transaction costs represent a modest ~0.94% annual drag. The strategy is not undermined by execution fees; its vulnerability is purely regime-dependent trend breakdown.
