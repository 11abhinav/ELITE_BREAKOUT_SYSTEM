# 54-STOCK SOURCE-AVAILABILITY & FORENSIC VALUATION DATA AUDIT
**Strategy Target**: `QUALITY_COMPOUNDER_VALUE_V2_FINAL`  
**Execution Date**: 2026-09-30  
**Data Universe**: 886 Approved Equities | 796 Point-in-Time (PIT) Equities  
**Scope**: 54 Equities with Unpopulated `current_ev_ebitda` Multiple in Latest PIT Scan  

---

## 1. EXECUTIVE SUMMARY & KEY FINDINGS

1. **Zero Valuation Stage Blockage**:
   - In the latest production scan, exactly **161 stocks passed the Quality Gate**.
   - Exactly **161 stocks were evaluated at the Valuation Gate** (40 Passed with $\ge 25\%$ EV/EBITDA discount vs 3Y median; 121 Rejected with $< 25\%$ discount).
   - Exactly **0 stocks were blocked by missing valuation data** at the decision gate (`Valuation Blocked = 0`).
   - Therefore, the 54 stocks with unpopulated current EV/EBITDA **did not prevent any eligible stock from qualifying or receiving a production alert**.

2. **Decomposition of the 54 Unpopulated Symbols**:
   - **42 Symbols (77.8%) — Financial Sector Companies (`FINANCIAL_NOT_APPLICABLE`)**:
     - Includes 33 commercial/public/private banks and 9 specialized NBFC, housing finance, and microfinance lenders (`AADHARHFC`, `AAVAS`, `ABCAPITAL`, `AIIL`, `APTUS`, `AUBANK`, `AXISBANK`, `BAJFINANCE`, `BANKBARODA`, `BANKINDIA`, `CGCL`, `CHOLAFIN`, `CHOLAHLDNG`, `CREDITACC`, `FEDERALBNK`, `FIVESTAR`, `HDBFS`, `HDFCBANK`, `HUDCO`, `ICICIBANK`, `INDIANB`, `INDIASHLTR`, `IREDA`, `J&KBANK`, `KOTAKBANK`, `KTKBANK`, `LICHSGFIN`, `LTF`, `M&MFIN`, `MASFIN`, `MUTHOOTFIN`, `NORTHARC`, `PFC`, `PNBHOUSING`, `RECLTD`, `REPCOHOME`, `SATIN`, `SBIN`, `SHRIRAMFIN`, `SUNDARMFIN`, `TATACAP`, `UNIONBANK`).
     - Under Rule 7 & Rule 2 of the frozen V2 specification, financial institutions are structurally excluded from EV/EBITDA because debt constitutes their primary operating raw material (deposits/borrowings) and net interest margin is their revenue, rendering EV/EBITDA economically undefined.
     - In the scanner, these are correctly tagged `METRIC_NOT_APPLICABLE_FINANCIAL`.
   - **12 Symbols (22.2%) — Non-Financial Companies with Earlier Gate Failures (`NOT_RELEVANT_TO_ALERT_DECISION` / `RAW_DATA_INSUFFICIENT`)**:
     - Names: `BENGALASM`, `FRONTSP`, `GOCLCORP`, `GODREJPROP`, `GUJTHEM`, `KALAMANDIR`, `KIRIINDUS`, `MAHLIFE`, `PFIZER`, `SIGNATURE`, `UTLSOLAR`, `ZSARACOM`.
     - **None of these 12 stocks reached or passed the Quality Gate**. All were eliminated by upstream hard gates:
       - `PFIZER`: Fails 5Y Sales CAGR (1.05% < 8%) and CFO/PAT (0.51 < 0.70).
       - `GODREJPROP`: Fails 5Y ROCE (6.4% < 12%) and CFO/PAT (-1.43 < 0.70).
       - `MAHLIFE`: Fails 5Y ROCE (3.0% < 12%) and CFO/PAT (-1.31 < 0.70).
       - `KIRIINDUS`: Fails 5Y ROCE (8.0% < 12%), Sales CAGR (-5.92%), and CFO/PAT (0.62).
       - `GOCLCORP`: Fails 5Y ROCE (6.4%), Sales CAGR (-87.2%), and CFO/PAT (-0.01).
       - `SIGNATURE`: Fails 5Y ROCE (0.4% < 12%) and D/E (1.61 > 1.50).
       - `UTLSOLAR`: Fails CFO/PAT (-0.05 < 0.70).
       - `BENGALASM`, `FRONTSP`, `GUJTHEM`, `KALAMANDIR`, `ZSARACOM`: Fail liquidity, market cap, or missing multi-year quality history.

3. **Data Availability Distinction**:
   - "Unpopulated in current PIT parquet" $\neq$ "Data does not exist anywhere".
   - Upstox officially documents and exposes direct EV/EBITDA via:
     `GET /v2/fundamentals/{isin}/key-ratios`
     along with raw balance sheet and income statement endpoints (`GET /v2/fundamentals/{isin}/financial-statements/balance-sheet` and `/income-statement`).
   - For all 54 symbols, live CMP prices exist in certified 1D Upstox history, and raw shares outstanding exist.

---

## 2. 54-STOCK SOURCE-AVAILABILITY & FORENSIC AUDIT TABLE

| Symbol | Financial? | Direct Upstox EV/EBITDA | Shares | Debt | Cash | EBITDA | CMP (₹) | Can Independently Calc? | Scanner Status | Reached Quality Gate? | Quality Gate Status | Gate Rejection Reasons | Final Classification |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :--- |
| **AADHARHFC** | YES | Documented API | YES | NO | NO | NO | ₹444.50 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **AAVAS** | YES | Documented API | YES | NO | NO | NO | ₹1267.70 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **ABCAPITAL** | YES | Documented API | YES | NO | NO | NO | ₹390.00 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **AIIL** | YES | Documented API | YES | NO | NO | NO | ₹559.60 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **APTUS** | YES | Documented API | YES | NO | NO | NO | ₹244.60 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **AUBANK** | YES | Documented API | YES | NO | NO | NO | ₹1018.00 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **AXISBANK** | YES | Documented API | YES | NO | NO | NO | ₹1222.40 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **BAJFINANCE** | YES | Documented API | YES | NO | NO | NO | ₹996.90 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **BANKBARODA** | YES | Documented API | YES | NO | NO | NO | ₹235.26 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **BANKINDIA** | YES | Documented API | YES | NO | NO | NO | ₹137.94 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **BENGALASM** | NO | Documented API | YES | NO | NO | NO | ₹6093.00 | NO | Missing | NO | FAIL | `ROCE_MISSING, SALES_CAGR_MISSING` | `RAW_DATA_INSUFFICIENT` |
| **CGCL** | YES | Documented API | YES | NO | NO | NO | ₹253.70 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **CHOLAFIN** | YES | Documented API | YES | NO | NO | NO | ₹1662.70 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **CHOLAHLDNG** | YES | Documented API | YES | NO | NO | NO | ₹1400.60 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **CREDITACC** | YES | Documented API | YES | NO | NO | NO | ₹1298.80 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **FEDERALBNK** | YES | Documented API | YES | NO | NO | NO | ₹322.00 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **FIVESTAR** | YES | Documented API | YES | NO | NO | NO | ₹520.95 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **FRONTSP** | NO | Documented API | YES | NO | NO | NO | ₹1438.80 | NO | Missing | NO | FAIL | `ROCE_MISSING, SALES_CAGR_MISSING` | `RAW_DATA_INSUFFICIENT` |
| **GOCLCORP** | NO | Documented API | YES | YES | NO | NO | ₹384.10 | NO | Missing | NO | FAIL | `FAIL_ROCE (6.4%), FAIL_SALES_CAGR (-87.2%), FAIL_CFO_PAT` | `RAW_DATA_INSUFFICIENT` |
| **GODREJPROP** | NO | Documented API | YES | YES | NO | NO | ₹1700.10 | NO | Missing | NO | FAIL | `FAIL_ROCE (6.4%), FAIL_CFO_PAT (-1.43)` | `RAW_DATA_INSUFFICIENT` |
| **GUJTHEM** | NO | Documented API | YES | NO | NO | YES | ₹417.00 | YES | Missing | NO | FAIL | `ROCE_MISSING, SALES_CAGR_MISSING, FAIL_CFO_PAT` | `NOT_RELEVANT_TO_ALERT_DECISION` |
| **HDBFS** | YES | Documented API | YES | NO | NO | NO | ₹648.15 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **HDFCBANK** | YES | Documented API | YES | NO | NO | NO | ₹735.60 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **HUDCO** | YES | Documented API | YES | NO | NO | NO | ₹173.99 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **ICICIBANK** | YES | Documented API | YES | NO | NO | NO | ₹1326.80 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **INDIANB** | YES | Documented API | YES | NO | NO | NO | ₹832.00 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **INDIASHLTR** | YES | Documented API | YES | NO | NO | NO | ₹654.30 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **IREDA** | YES | Documented API | YES | NO | NO | NO | ₹113.22 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **J&KBANK** | YES | Documented API | YES | NO | NO | NO | ₹146.10 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **KALAMANDIR** | NO | Documented API | YES | NO | NO | YES | ₹77.19 | YES | Missing | NO | FAIL | `ROCE_MISSING, FAIL_SALES_CAGR (-1.72%), FAIL_CFO_PAT` | `NOT_RELEVANT_TO_ALERT_DECISION` |
| **KIRIINDUS** | NO | Documented API | YES | YES | NO | NO | ₹557.50 | NO | Missing | NO | FAIL | `FAIL_ROCE (8.0%), FAIL_SALES_CAGR (-5.92%), FAIL_CFO_PAT` | `RAW_DATA_INSUFFICIENT` |
| **KOTAKBANK** | YES | Documented API | YES | NO | NO | NO | ₹404.00 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **KTKBANK** | YES | Documented API | YES | NO | NO | NO | ₹335.50 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **LICHSGFIN** | YES | Documented API | YES | NO | NO | NO | ₹566.00 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **LTF** | YES | Documented API | YES | NO | NO | NO | ₹284.00 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **M&MFIN** | YES | Documented API | YES | NO | NO | NO | ₹333.10 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **MAHLIFE** | NO | Documented API | YES | YES | NO | NO | ₹356.85 | NO | Missing | NO | FAIL | `FAIL_ROCE (3.0%), FAIL_CFO_PAT (-1.31)` | `RAW_DATA_INSUFFICIENT` |
| **MASFIN** | YES | Documented API | YES | NO | NO | NO | ₹282.75 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **MUTHOOTFIN** | YES | Documented API | YES | NO | NO | NO | ₹2835.00 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **NORTHARC** | YES | Documented API | YES | NO | NO | NO | ₹300.90 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **PFC** | YES | Documented API | YES | NO | NO | NO | ₹338.00 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **PFIZER** | NO | Documented API | YES | YES | YES | NO | ₹4000.80 | NO | Missing | NO | FAIL | `FAIL_SALES_CAGR (1.05%), FAIL_CFO_PAT (0.51)` | `RAW_DATA_INSUFFICIENT` |
| **PNBHOUSING** | YES | Documented API | YES | NO | NO | NO | ₹1094.90 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **RECLTD** | YES | Documented API | YES | NO | NO | NO | ₹312.95 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **REPCOHOME** | YES | Documented API | YES | NO | NO | NO | ₹347.70 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **SATIN** | YES | Documented API | YES | NO | NO | NO | ₹236.65 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **SBIN** | YES | Documented API | YES | NO | NO | NO | ₹983.00 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **SHRIRAMFIN** | YES | Documented API | YES | NO | NO | NO | ₹994.10 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **SIGNATURE** | NO | Documented API | YES | YES | NO | NO | ₹788.85 | NO | Missing | NO | FAIL | `FAIL_ROCE (0.4%), FAIL_DE (1.61 > 1.50)` | `RAW_DATA_INSUFFICIENT` |
| **SUNDARMFIN** | YES | Documented API | YES | NO | NO | NO | ₹4508.00 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **TATACAP** | YES | Documented API | YES | NO | NO | NO | ₹339.15 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **UNIONBANK** | YES | Documented API | YES | NO | NO | NO | ₹180.20 | NO | Missing | NO | FAIL | `METRIC_NOT_APPLICABLE_FINANCIAL` | `FINANCIAL_NOT_APPLICABLE` |
| **UTLSOLAR** | NO | Documented API | YES | NO | NO | YES | ₹422.10 | YES | Missing | NO | FAIL | `FAIL_CFO_PAT (-0.05 < 0.70)` | `NOT_RELEVANT_TO_ALERT_DECISION` |
| **ZSARACOM** | NO | Documented API | YES | NO | NO | NO | ₹10307.00 | NO | Missing | NO | FAIL | `ROCE_MISSING, SALES_CAGR_MISSING, FAIL_CFO_PAT` | `RAW_DATA_INSUFFICIENT` |

---

## 3. TELEMETRY CLARIFICATION & REMEDIATION

### Prior Confusing Telemetry:
```text
Valuation Gate Blocked (no data): 0  (CURRENT_EV_EBITDA_MISSING=54 | EV_EBITDA_3Y_MEDIAN_MISSING=7 | Both_Missing=7)
```
*Why this was misleading*: Readers interpreted `CURRENT_EV_EBITDA_MISSING=54` as 54 quality-passed candidates being blocked at the decision gate, when in truth exactly 0 candidates were blocked.

### Remediated Clean Telemetry Structure (in `app/live_fundamental_scanner.py`):
```text
  3. STRATEGY FILTER FUNNEL RECONCILIATION:
     • Quality-evaluated PIT symbols  : 651  (PIT symbols with full ROCE/CAGR/CFO/D_E history)
     • Quality Gate Passed            : 161
     • Quality Gate Rejected          : 490  (failed ROCE, CAGR, CFO, debt, or liquidity)
       [Identity: 161 Passed + 490 Rejected = 651 Quality-evaluated]  ✅
     • UNIVERSE VALUATION DATA GAPS (diagnostic across entire PIT universe):
         ├─ Current EV/EBITDA missing : 54
         ├─ 3Y median missing         : 7
         └─ Both missing (overlap)    : 7
     • QUALITY-PASSED STOCKS VALUATION FUNNEL:
         ├─ Valuation evaluated       : 161
         ├─ Valuation passed          : 40  (EV/EBITDA discount >= 25%)
         ├─ Valuation rejected        : 121  (EV/EBITDA discount < 25%)
         └─ Valuation blocked by data : 0
       [Identity: 40 Val-Pass + 121 Val-Reject + 0 Val-Blocked = 161 Quality-Passed]  ✅
```
This correctly isolates universe-level diagnostic data gaps from candidate-level evaluation outcomes.
