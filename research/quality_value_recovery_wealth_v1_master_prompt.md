# MASTER STRATEGY RESEARCH PROMPT

## STRATEGY NAME
**QUALITY_VALUE_RECOVERY_WEALTH_V1**

## Strategy Objective
Research and backtest a long-term wealth-building strategy designed to identify high-quality companies that are fundamentally healthy or improving, but whose market valuation and/or share price has fallen materially below its own historical levels without a corresponding structural deterioration in the business.

The objective is **not** to predict short-term price movements.

**The objective is to answer:**
When a fundamentally strong/improving company experiences a meaningful valuation compression or price correction, and there is evidence that the business itself has not structurally deteriorated, does buying after the correction create attractive long-term returns?

The strategy should attempt to catch these companies after the correction has stabilized, rather than buying simply because the stock has fallen.

The intended holding period is **long term**.
Do not use fixed profit targets as the primary exit mechanism.

**The intended philosophy is:**
Buy quality during temporary weakness → allow the business to compound → stay invested while business and price structure remain healthy → exit only when the investment thesis becomes structurally broken.

---

### 1. CORE HYPOTHESIS TO TEST
Test the following hypothesis independently and as a combined strategy:

**H1 — Quality survives the correction**
A company experiencing a substantial price/valuation decline can remain fundamentally healthy.

**H2 — Fundamentals improve while valuation contracts**
Some of the strongest opportunities may occur when:
- Sales continue increasing
- Margins improve or remain resilient
- EBITDA/PAT improve
- Cash generation improves
- Debt reduces
- ROCE/ROIC remains high or improves
- EPS improves
- Return on equity remains healthy
- The business continues generating cash
**But** share price, P/E, EV/EBITDA or other valuation measures decline materially from historical levels.

**H3 — Temporary market weakness creates mispricing**
The price decline may be driven by:
- broad market weakness
- sector weakness
- temporary sentiment
- multiple compression
- profit booking
- temporary cyclical concerns
- market-wide de-rating
- temporary technical damage
...rather than permanent business deterioration.

**H4 — Buying after confirmation is superior to buying during the initial fall**
Do NOT assume that the lowest price is the best entry.
Explicitly test different entry methods:
- Immediate valuation-based entry
- Entry after a defined drawdown
- Entry after stabilization
- Entry after technical recovery
- Entry after relative-strength recovery
- Entry after valuation compression + fundamental confirmation
- Hybrid entry combining fundamentals + valuation + price structure

**H5 — Long-term holding creates asymmetric wealth**
Test whether these opportunities generate attractive returns when held until:
- structural fundamental deterioration
- persistent earnings deterioration
- major balance-sheet deterioration
- loss of business quality
- major long-term technical deterioration
- prolonged break of critical long-term price structure
...rather than exiting simply because the stock has already appreciated.

---

### 2. ABSOLUTE DATA-INTEGRITY RULE
This research must follow a strict **point-in-time** backtest.
**NEVER use future information.**

Every investment decision at date T must use only information that was genuinely available to an investor by the decision time.
For every fundamental data item determine:
- financial period end
- filing/publication date
- actual availability date
- source
- whether consolidated or standalone
- whether original or subsequently revised
- whether the value was available at the historical decision date

**Prohibitions:**
- Do NOT allow a financial statement published after the entry date to influence the historical entry decision.
- Do NOT use today's financial database values to reconstruct historical decisions unless the database explicitly preserves point-in-time availability.
- Do NOT use future annual results to calculate historical growth.
- Do NOT use future price highs/lows.
- Do NOT use future moving averages.
- Do NOT use future valuation medians.
- Do NOT use future stock classifications, index membership, or sector classifications.
- Do NOT use future corporate-action information in a way that would not have been available at the time.

---

### 3. SURVIVORSHIP-BIAS PROTECTION
The universe must be reconstructed historically.
Do NOT simply run the strategy today against today's surviving NSE/BSE universe.

Include, wherever data permits:
- companies that subsequently delisted
- companies that were acquired
- companies that merged
- companies that later went bankrupt
- companies that were suspended
- companies that later became very successful
- companies that disappeared from today's universe

Use historical universe membership for each test date. Report survivorship-bias limitations explicitly if complete historical universe reconstruction is not possible.

---

### 4. CORPORATE ACTION ADJUSTMENT
Prices must be adjusted consistently for:
- splits
- bonuses
- rights issues
- mergers/demergers where applicable
- dividends where appropriate
- symbol changes
- corporate restructurings

Make sure adjusted prices do not accidentally create artificial drawdowns or returns.

---

### 5. PRIMARY INVESTMENT UNIVERSE
Start with the broadest reliable historical universe available.
**Prefer:** NSE/BSE listed non-financial operating companies.

Evaluate whether financial companies require a separate model because conventional ROCE, EV/EBITDA and debt metrics are not economically comparable. Do not force financial companies through non-financial quality rules.

---

### 6. DEFINE "QUALITY COMPANY"
Build a comprehensive quality framework. Do NOT rely on a single metric.

Evaluate at minimum:
- **Growth:** Revenue/Sales growth, 3Y Sales CAGR, 5Y Sales CAGR, EBITDA growth, PAT growth, EPS growth, Operating cash-flow growth
- **Profitability:** ROCE, ROIC where available, ROE, EBITDA margin, EBIT margin, PAT margin
- **Margin trend:** current margin, 3Y average margin, 5Y average margin, margin expansion/contraction, recent margin acceleration/deceleration. Test both absolute margin quality and direction of change in margins.
- **Balance sheet:** Debt/Equity, Net debt, Net debt/EBITDA, interest coverage, debt trend, debt reduction rate, cash balance, net cash where applicable.
- **Cash-flow quality:** CFO, PAT, CFO/PAT, cumulative CFO/PAT, free cash flow, FCF trend, operating cash conversion.
- **Capital efficiency:** ROCE trend, ROE trend, incremental ROCE if possible, reinvestment efficiency where data permits.
- **Earnings consistency:** consistency of annual earnings, number of profitable years, frequency of earnings declines, magnitude of earnings declines, recovery after prior declines.

---

### 7. "IMPROVING BUSINESS" SCORE
Create a separate Fundamental Improvement Score. Do not merely classify companies as good/bad. Measure whether the business is improving.

Construct multiple versions:
1. **Quality Stable:** Business quality remains strong.
2. **Quality Improving:** Multiple fundamental dimensions are improving.
3. **Quality Deteriorating:** Multiple fundamental dimensions are deteriorating.

---

### 8. DEFINE "VALUATION CHEAP RELATIVE TO ITS OWN HISTORY"
Do NOT simply define cheap as `PE < 20` because valuation differs massively by company. Measure valuation relative to the company's own history.

**Test metrics:**
- P/E (vs 1Y, 3Y, 5Y, 7Y, 10Y medians)
- EV/EBITDA (vs historical medians)
- P/B, Price/Sales, Earnings yield, Free-cash-flow yield (where data permits)

**Create derived metrics:**
- `PE Discount = 1 - (Current PE / Historical PE Median)`
- `EVEBITDA Discount = 1 - (Current EVEBITDA / Historical EVEBITDA Median)`

---

### 9. DISTINGUISH CHEAP FROM VALUE TRAP
Explicitly divide opportunities into:
A. **Quality + valuation compression:** Business healthy/improving + valuation cheap.
B. **Quality + price correction:** Business healthy/improving + price materially down.
C. **Quality + valuation + price correction:** All three conditions.
D. **Cheap but deteriorating:** Low valuation + deteriorating business.
E. **Severe deterioration / value trap:** Cheap valuation + major fundamental deterioration.

Compare future returns across these groups.

---

### 10. DEFINE THE CORRECTION
Test several correction definitions.
- **Price drawdown:** From recent/high watermark (-10%, -15%, -20%, -30%, -40%).
- **Relative valuation compression:** e.g., Current EV/EBITDA <= 80% of 5Y median.
- **Combined correction:** Price drawdown >= 20% AND valuation below median AND fundamentals remain healthy.

All thresholds must be defined before evaluating the corresponding test results.

---

### 11. DO NOT BUY THE FIRST FALL
Explicitly test the timing problem:
- **Entry Model A — Immediate:** Buy when all fundamental + valuation conditions are first satisfied.
- **Entry Model B — Stabilization:** Wait for evidence that the fall has stopped accelerating.
- **Entry Model C — Technical Recovery:** Wait for price recovery through defined technical levels.
- **Entry Model D — Relative Strength Recovery:** Wait for stock relative strength versus benchmark/sector to stabilize and improve.
- **Entry Model E — Hybrid:** Fundamental quality + valuation compression + correction + technical stabilization/recovery.

---

### 12. MULTIPLE TIME FRAME ANALYSIS
Evaluate the strategy across Daily, Weekly, and Monthly states. Do not assume the strongest technical filter is automatically the best wealth-building rule. Measure the trade-off between earlier entries, lower entry valuation, false bottoms, drawdown, and long-term CAGR.

---

### 13. ENTRY RULE
Create several pre-registered entry formulations.

**Example baseline:**
`NON-FINANCIAL COMPANY` AND `Quality metrics satisfy minimum requirements` AND `fundamental improvement/stability confirmed` AND `valuation materially below own historical valuation` AND `price has corrected materially` AND `no major structural fundamental deterioration` AND `correction has stabilized` AND `technical structure is not catastrophically broken`

Entry execution: Signal at Date T Close → Fill at Date T+1 Open. Apply realistic slippage and transaction costs.

---

### 14. ENTRY CONFIRMATION
Test multiple confirmation mechanisms:
- **Price stabilization:** no new lower low for N sessions, higher low formation, close above short-term average, reclaim of moving average.
- **Relative strength:** Stock begins outperforming Nifty or sector index.
- **Momentum recovery:** RSI recovery, rate-of-change stabilization, moving-average recovery.

---

### 15. EXIT PHILOSOPHY
This is intentionally not a fixed-target strategy.
**The primary objective is:** Hold the stock while the underlying investment thesis remains intact.
Do NOT automatically exit at +20%, +50%, or +100% because large wealth creation may require holding exceptional companies for years.

---

### 16. FUNDAMENTAL EXIT CONDITIONS
Potential exit triggers:
- **Business deterioration:** sustained revenue decline, sustained PAT decline, sustained margin deterioration, significant ROCE collapse, CFO/PAT deterioration, FCF deterioration, debt expansion.
- **Structural change:** business model deterioration, persistent competitive deterioration, severe earnings downgrade.

Test persistence rules (e.g., 1 period vs 2 consecutive periods vs 3 consecutive periods).

---

### 17. TECHNICAL STRUCTURAL EXIT
Test long-term structural exits such as:
- Daily: sustained close below SMA200
- Weekly: sustained close below Weekly SMA40/50
- Long-term trend breakdown: lower highs + lower lows, prolonged relative-strength deterioration.

Test persistence (1 close, 3 closes, 5 closes, 2 weeks, 4 weeks).

---

### 18. COMBINATION EXIT
Determine which architecture best preserves long-term winners without allowing structurally broken companies to remain indefinitely by testing variations of `FUNDAMENTAL DETERIORATION` AND/OR `STRUCTURAL PRICE BREAKDOWN`.

---

### 19. MAXIMUM DRAWDOWN ANALYSIS
For every position calculate MAE, Maximum Drawdown after entry, Time to recovery, Time underwater, Worst 1M/3M/6M/1Y return, and eventual return.

---

### 20. HOLDING-PERIOD ANALYSIS
Analyze 1M, 3M, 6M, 1Y, 2Y, 3Y, 5Y, 7Y, 10Y. For each holding horizon calculate median/mean return, CAGR, win rate, maximum drawdown, and probability of loss.

---

### 21. REGIME ANALYSIS
Backtest separately across Bull, Bear, Sideways, High-volatility, and Low-volatility markets. Use objective historical regime definitions. Do NOT define regimes after viewing strategy performance.

---

### 22. MARKET-CORRECTION OPPORTUNITY ANALYSIS
Explicitly investigate periods where NIFTY falls significantly AND QUALITY COMPANY falls significantly BUT FUNDAMENTALS remain healthy.

---

### 23. SECTOR ANALYSIS
Break results down by sector. Determine whether the hypothesis works differently in IT, pharma, chemicals, consumer, industrials, capital goods, etc.

---

### 24. VALUATION NORMALIZATION ANALYSIS
Test whether future returns are related to the extent of valuation compression by creating buckets (0-10% discount, 10-20% discount, etc.).

---

### 25. FUNDAMENTAL QUALITY VS VALUATION MATRIX
Create a matrix of Quality State (Strong, Improving, Deteriorating) vs Valuation State (Expensive, Fair, Cheap) and observe outcomes.

---

### 26. CROSS-SECTIONAL COMPARISON
For every qualifying opportunity, compare it against the Benchmark (NIFTY 50), Sector benchmark, and Non-qualifying stocks.

---

### 27. BENCHMARK-CORRECTED RETURNS
For every trade calculate Absolute Return, Benchmark-relative Return, and Sector-relative Return.

---

### 28. RESEARCH DESIGN
Use strict chronological separation:
- **Discovery / training period:** 2010/2011 onward for hypothesis development.
- **Out-of-sample period:** Reserve a later chronological period that is never used for threshold selection.
- **Forward holdout:** Reserve the most recent period completely untouched until the strategy is frozen.

---

### 29. NO DATA-MINING RULE
Do NOT repeatedly modify thresholds after seeing results.
Any threshold discovered after inspecting results must be classified as research-only until independently validated.

---

### 30. PARAMETER ROBUSTNESS
Do not report only the best-performing configuration. For every important parameter, show nearby values to determine whether results are robust across neighboring values.

---

### 31. STATISTICAL VALIDATION
Calculate standard robust metrics (bootstrap confidence intervals, Sharpe, Sortino, max drawdown, CAGR, Calmar ratio, win rate, payoff ratio, etc.). Emphasize expectancy + CAGR + drawdown + capital survival + right-tail winners.

---

### 32. PORTFOLIO SIMULATION
Test a realistic portfolio simulation including Equal weight, Maximum number of simultaneous positions, Position concentration limits, Sector concentration limits, Re-entry rules, and Capital allocation.

---

### 33. WEALTH-CREATION TEST
Run a hypothetical capital-growth simulation (e.g., Starting capital ₹10 lakh). Calculate ending capital, CAGR, maximum drawdown, time underwater, worst calendar year.

---

### 34. WINNER CONCENTRATION ANALYSIS
Determine Top 1/5/10 winner contribution and percentage of total profits from top winners. Test whether the strategy still works without a handful of extraordinary multibaggers.

---

### 35. EXIT FORENSICS
For every winning and losing trade, reconstruct the exact states at entry, peak, and exit. Determine whether the exit engine exits too early, stays too long, or accidentally removes long-term compounders.

---

### 36. FAILURE ANALYSIS
For every losing position, classify the failure mode (Value Trap, Fundamental Deterioration, Correction Continued, Market Crash, Technical False Recovery, etc.). Test whether that failure could have been detected using only information available at entry.

---

### 37. SPECIAL TEST — "NOTHING WRONG WITH THE COMPANY"
Create a dedicated subgroup of companies with healthy fundamentals but substantially fallen price/valuation. This is the core hypothesis test.

---

### 38. SPECIAL TEST — FALLING KNIFE VS CORRECTED OPPORTUNITY
Separate Falling Knife, Early Recovery, Confirmed Recovery, and Re-rated stages. Compare returns across these stages to reveal when the opportunity actually becomes investable.

---

### 39. MULTI-TIMEFRAME ENTRY TOURNAMENT
Run separate models (Fundamentals only, + daily trend, + weekly trend, + relative strength, + stabilization, etc.) and compare return, drawdown, robustness, missed winners, and turnover.

---

### 40. RE-ENTRY ANALYSIS
Determine whether a company can generate multiple valid opportunities. Define a meaningful re-entry cooldown and test robustness.

---

### 41. DATA PROVIDENCE REQUIREMENT
Preferred hierarchy: Historical exchange price data + historical corporate filings + verified fundamental source + independent cross-check.
Never manufacture a signal. If required historical data cannot be established, fail the decision.

---

### 42. FUNDAMENTAL RECONCILIATION
If critical sources disagree materially, throw a `DATA_CONFLICT`. Do not silently choose whichever value improves the backtest.

---

### 43. BACKTEST EXECUTION TIMING
Enforce: Information available by Date T → Signal generated → Decision frozen → Execution = Date T+1 Open. No later information may influence entry.

---

### 44. RESULTS REQUIRED
Produce a complete research report containing an executive conclusion (SUPPORTED, PARTIALLY SUPPORTED, NOT SUPPORTED, REJECTED), core statistics, regime results, valuation results, correction results, quality results, exit/entry timing results, and walk-forward results.

---

### 45. FINAL PROMOTION GATE
Do NOT recommend production implementation simply because one backtest is profitable. The strategy should only progress through the 10 defined integrity gates (Data Integrity, No Lookahead, Survivorship Control, Statistical Power, Robustness, OOS, Forward Holdout, Economic Logic, Failure Analysis, Paper Validation).

---

### 46. IMPORTANT: DO NOT FORCE A STRATEGY TO WORK
It is acceptable for the final conclusion to be that the strategy does not generate sufficient evidence. Do not modify the model merely to obtain attractive CAGR.

---

### 47. FINAL QUESTION THE RESEARCH MUST ANSWER
At the end, answer the 15 core questions explicitly laid out in the strategy spec.

---

## FINAL DELIVERABLE
Produce a **Complete forensic research report** and an **Exact frozen strategy specification** (only if the evidence supports one). The final strategy specification must be deterministic and implementable in code. Do not leave critical decision rules ambiguous.

**Key Rule:** The real research object is:
`QUALITY + FUNDAMENTAL IMPROVEMENT / STABILITY + VALUATION COMPRESSION + MEANINGFUL PRICE CORRECTION + EVIDENCE OF STABILIZATION → LONG-TERM HOLD → EXIT ONLY ON STRUCTURAL BREAK`

*Keep the strategy RESEARCH ONLY until chronological OOS + untouched forward holdout are proven.*
