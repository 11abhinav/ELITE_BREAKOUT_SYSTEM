# FUNDAMENTAL_GEM_RECOVERY_V5 — RESEARCH BLUEPRINT

> [!WARNING]
> **SUPERSEDED & UNEXECUTED**: This blueprint was written prior to verifying the Phase B pre-registered breadth floor pass, contrary to protocol. Because the breadth floor failed (51.7% of months with $\ge 8$ candidates vs required 60.0%; 1 independent episode vs required 3), this backtest blueprint is **SUPERSEDED BY VERDICT (`INCONCLUSIVE_BREADTH`) AND WAS NOT EXECUTED**. It is preserved in the research record for historical audit purposes only.

> **STUDY TYPE**: Scored Feature-Rich Undervaluations Funnel & Forward Tracking Watchlist (NOT EXECUTED)  
> **PRIMARY ARM**: Non-Financial Quality & Valuation Discount ($L1 + L2 + L3_{\text{EV/EBITDA} \ge 25\%}$)  
> **SECONDARY DIAGNOSTIC ARM**: Financials (Bank/NBFC using ROE & P/B)  
> **SECONDARY DISLOCATION COMPARISON**: Picks with $Res\_DD \le 10\%$ vs Picks without $Res\_DD \le 10\%$  
> **PROVENANCE GATE**: `UPSTOX_REAL_DATA_ONLY` | **RESOURCES**: `pit_fundamentals_v1.parquet` (Annual point-in-time)  

---

### 1. Pre-Registered Funnel Architecture

#### Layer 1: Business Quality (Hard Gate)
- **ROCE 3Y Average**: $\ge 15.0\%$ (Non-financials) / **ROE 3Y Average**: $\ge 15.0\%$ (Financials diagnostic)
- **3Y Sales CAGR**: $\ge 10.0\%$
- **3Y PAT CAGR**: $\ge 10.0\%$
- **Cumulative CFO / PAT (3Y)**: $\ge 0.80$
- **Debt / Equity**: $\le 0.50$ (Non-financials)

#### Layer 2: Forensic & Dilution Safeguard (Hard Gate)
- **Share Dilution (3Y)**: $\le 10.0\%$ cumulative expansion in shares outstanding.

#### Layer 3: Anti-Cyclical Valuation Discount (Hard Gate)
- **Gating Metric**: $\text{EV/EBITDA}_{\text{current}} \le 0.75 \times \text{EV/EBITDA}_{\text{stock\_own\_3Y\_median}}$ ($\ge 25\%$ discount to stock's own trailing 3Y point-in-time median).
- **Scored Feature (Not Gate)**: $P/E_{\text{norm}}$ discount to stock's own trailing 3Y median.

#### Layer 4: Dislocation & Residual Drawdown (Scored Feature Set — Not Gate)
- **Stock Peak Drawdown ($\text{DD}_{\text{stock}}$)**: Recorded.
- **Sector Index Drawdown ($\text{DD}_{\text{sector}}$)**: Recorded (flagged if sector constituents $< 10$).
- **Rolling 3Y Weekly Beta ($\beta_{i/\text{sector}}$)**: Recorded.
- **Residual Drawdown ($\text{Res\_DD}$)**: $\text{DD}_{\text{stock}} - (\beta \times \text{DD}_{\text{sector}})$.
- **Secondary Arm Filter**: $Res\_DD \le 10\%$ vs $Res\_DD > 10\%$.

---

### 2. Candidate Scoring & Top-15 Selection Logic

Each monthly candidate passing $L1 + L2 + L3_{\text{EV/EBITDA}}$ is assigned a **100-Point Quality-Valuation Score**:
1. **EV/EBITDA Discount Depth** (Max 30 pts): $30 \times \min\left(\frac{\text{Discount} - 0.25}{0.25}, 1.0\right)$
2. **3Y Average ROCE** (Max 25 pts): $25 \times \min\left(\frac{\text{ROCE} - 15.0}{25.0}, 1.0\right)$
3. **PE\_norm Discount Depth** (Max 20 pts): $20 \times \min\left(\frac{\text{PE\_Discount}}{0.40}, 1.0\right)$
4. **CFO / PAT Cash Quality** (Max 15 pts): $15 \times \min\left(\frac{\text{CFO\_PAT} - 0.80}{0.70}, 1.0\right)$
5. **Residual Drawdown Bonus** (Max 10 pts): $10 \times \min\left(\frac{0.25 - \text{Res\_DD}}{0.25}, 1.0\right)$

- **Monthly Portfolio Selection**: Top 15 stocks by 100-point score (Tie-break: Higher 3Y Average ROCE).

---

### 3. Reporting & Governance Rules

1. **Sub-Period Reporting**: Results reported by calendar year (2020, 2021, 2022, 2023, 2024) AND **excluding 2020** (to isolate non-COVID recovery edge).
2. **Secondary Arm Comparison**: Top-15 picks with $Res\_DD \le 10\%$ vs picks without $Res\_DD \le 10\%$, using identical exits.
3. **Holdout Window**: 2025-01-01 to 2026-06-30 (Report only, zero tuning).

---

### 4. Phase E Live Forward Tracker & Manual Pre-Buy Checklist

#### Watchlist Tiers
- **Tier A**: Passes $L1 + L2 + L3$ WITH $Res\_DD \le 10\%$ (Deep systemic dislocation).
- **Tier B**: Passes $L1 + L2 + L3$ WITHOUT $Res\_DD \le 10\%$ (Quality stock cheap vs own history).

#### Mandatory Manual Pre-Buy Checklist (Required for Live Placement)
Because structured XBRL point-in-time tables lack certain qualitative disclosures, live pre-buy requires manual verification of:
1. **Promoter Pledge**: Verify latest BSE/NSE pledge disclosure is $< 5\%$.
2. **Promoter Holding Trend**: Verify no sudden promoter exit or change of control in last 4 quarters.
3. **Auditor Integrity**: Verify no recent auditor qualification, emphasis of matter, or sudden resignation.
4. **Receivable Days**: Inspect receivables trend over last 3 years to ensure cash conversion.
5. **CWIP Ageing**: Ensure capital work-in-progress is not stuck $> 3$ years without commissioning.
6. **Related Party Transactions**: Verify RPT sales/purchases $< 10\%$ of revenue/operating expense.
7. **Latest Quarterly Results & Commentary**: Review management concall for structural degradation vs temporary cyclical hit.

#### Append-Only Performance Tracking Log
- **1Y / 2Y / 3Y Forward Returns** tracked continuously against:
  1. Nifty 500 TRI Benchmark
  2. Equal-Weight L1 Quality Basket Placebo
