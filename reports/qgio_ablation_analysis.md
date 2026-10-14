# QGIO-1 — Strategy Ablation & Component Attribution Analysis

**Candidate ID**: `QGIO_1`  
**Strategy Name**: Quality Growth + Verified Ownership Accumulation  
**Evaluation Span**: 2018-06-15 to 2026-10-12 (8.33 Years, 2,045 Trading Sessions)  
**Initial Capital**: ₹1,000,000  
**Research Objective**: Isolate the marginal value of (1) Fundamental Quality Growth, (2) Disclosed Ownership Accumulation, and (3) Price-Trend Confirmation.

---

## 1. The Three-Arm Ablation Architecture

To prevent false attribution where an incidental filter is credited for gains produced by underlying market beta or a generic quality factor, the strategy protocol prescribed a three-way comparative ablation:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             ABLATION ARMS                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│ Arm A (Baseline):  Quality Growth  +  50/200-day Trend Filter                │
│                    (Evaluates Quality + Technical Trend without Ownership)  │
├─────────────────────────────────────────────────────────────────────────────┤
│ Arm B (Ownership): Quality Growth  +  Verified Ownership (No Trend Filter)  │
│                    (Evaluates pure Institutional/Promoter Accumulation)     │
├─────────────────────────────────────────────────────────────────────────────┤
│ Arm C (Full Model):Quality Growth  +  Verified Ownership  +  50/200 Trend    │
│                    (Evaluates the Complete Triple-Gate Synthesis)           │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Comparative Ablation Performance Matrix

| Strategy Variant | Architecture | Total Trades | Win Rate (%) | Profit Factor | Net CAGR (%) | Total Return (%) | Sharpe Ratio | Max Drawdown (%) | Marginal Attribution Verdict |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Strategy A (Baseline)** | Quality Growth + 50/200 Trend | **235** | **32.3%** | **2.08** | **14.55%** | **+209.96%** | **0.49** | **-36.69%** | **Viable Engine** (Outperforms N500 TRI by +2.52% CAGR over 8.33 years) |
| **Strategy B (Ownership)**| Quality Growth + Ownership (No Trend)| **0** | **0.0%** | **0.00** | **0.00%** | **0.00%** | **0.00** | **0.00%** | **Unprovable** (0 historical transaction records in repository) |
| **Strategy C (Full Strategy)**| Quality + Ownership + 50/200 Trend | **0** | **0.0%** | **0.00** | **0.00%** | **0.00%** | **0.00** | **0.00%** | **Blocked on Data Non-Existence** (0 trades generated) |
| **Nifty 500 TRI** | Broad Market Benchmark | 0 | N/A | N/A | **12.03%** | **+133.56%** | **0.32** | **-38.30%** | Broad Indian equity benchmark |
| **Nifty500 Quality 50** | Official Quality Factor Index | 28 | 67.9% | 7.36 | **7.33%** | **+80.17%** | **0.13** | **-17.86%** | Institutional Quality benchmark |

---

## 3. Forensic Component Attribution Findings

### Finding 1: Ownership Accumulation (Gate 2) Adds Zero Demonstrable Value
- Under the frozen protocol:
  > *"If Strategy C does not improve meaningfully on Strategy A, we should reject the ownership overlay even if the overall portfolio makes money."*
- Because the repository possesses **zero historical records** for SEBI insider trading (PIT Reg 7(2)), historical bulk/block transactions, or historical quarterly shareholding patterns:
  - Strategy B executed **0 trades**.
  - Strategy C executed **0 trades**.
- It is mathematically and forensically impossible to claim that ownership accumulation adds an edge over Strategy A. The ownership overlay cannot be certified.

### Finding 2: Strategy A (Quality + Trend) Delivers Real Structural Compounding
- In the absence of an ownership filter, **Strategy A** demonstrates the power of combining business quality with technical trend alignment:
  - Compounded initial capital from ₹1,000,000 to **₹3,099,600** (+209.96% total return).
  - Outperformed **Nifty 500 TRI** by **+2.52% annualized** (14.55% vs 12.03%).
  - Doubled the performance of the **Nifty500 Quality 50 Proxy** (14.55% vs 7.33%).
  - Achieved an overall **Profit Factor of 2.08** across 235 executed positions.
- **Why Did Strategy A Succeed Over the Full Cycle?**
  1. **Non-Financial Concentration**: Excluded leveraged lenders and banks during NPA recognition cycles.
  2. **Trend Momentum Filter**: Only entered companies when Close > 50 SMA > 200 SMA and the 200 SMA was actively rising, ensuring that capital was deployed into winning trends rather than falling knives.
  3. **High Reinvestment Quality**: By avoiding high dividend payout screens (which crippled QPF-1), Strategy A participated in dynamic compounding businesses.

### Finding 3: The Trend Following Payoff Profile (Low Win Rate, Fat Tails)
- Strategy A achieved a win rate of only **32.3%** (76 winning trades out of 235).
- However, its Profit Factor was **2.08** because:
  - Winning trades compounded substantially while riding the 200 SMA trend (average winning trade gain was +38.4%).
  - Losing trades were cut systematically when the stock closed below the 200 SMA for 3 sessions (average losing trade loss was -9.1%).
- This confirms that Strategy A functions as a **trend-following momentum filter on a fundamental quality universe**, NOT a high-win-rate quality screen.

### Finding 4: The 200 SMA Invalidation Exit Lag & Whipsaw
- The 200 SMA exit rule (3 closes below 200 SMA) carries significant exit lag:
  - When high-flying growth stocks break down, they often drop 15% to 25% from their peak before penetrating the 200-day moving average.
  - In consolidating/choppy markets, stocks frequently breach the 200 SMA, triggering an exit, only to reclaim it weeks later, incurring round-trip friction.
  - This lag directly caused Strategy A's underperformance in the 2024–2026 holdout.

---

## 4. Summary Verdict on the Ownership Hypothesis

The hypothesis that *verified disclosed ownership accumulation creates a reproducible trading edge* **CANNOT BE VALIDATED** on the available repository data.

Strategy A (Quality Growth + Trend) is the sole viable mathematical engine, but as detailed in the Risk and Regime Report, it exhibits severe regime instability and fails the locked holdout gate.
