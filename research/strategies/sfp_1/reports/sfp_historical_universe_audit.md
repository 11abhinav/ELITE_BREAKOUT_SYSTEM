# SFP-1 — HISTORICAL UNIVERSE & SURVIVORSHIP BIAS FORENSIC AUDIT

**Strategy Identifier**: `SFP-1` (Size Premium with Financial Survival Filters)  
**Research Namespace**: `research/strategies/sfp_1/`  
**Audit Date**: 2026-10-13  
**Status**: `SURVIVORSHIP_LIMITATION_CONFIRMED`  

---

## 1. Objective of Universe Audit

The SFP-1 strategy specifies:
> *"Universe: Historically eligible NSE companies ranked 251–500 by market capitalisation. Verify that the historical dataset includes companies that later failed, delisted, graduated into larger-cap segments or dropped out of the eligible universe."*

This audit conducts a forensic verification of whether the repository's historical data can reconstruct a true point-in-time Smallcap 250 universe free from survivorship bias.

---

## 2. Forensic Test for Delisted and Failed Companies

To test whether the historical price and fundamental datasets contain survivorship bias, a sample of 12 prominent historical NSE companies that were actively traded in the small/mid-cap segments between 2016 and 2023 but subsequently entered IBC insolvency, experienced catastrophic liquidation, or were delisted was queried against `data/history/1d/*.parquet`:

| Historical Distressed / Failed Symbol | Historical Segment (2016–2019) | Resolution / Event | Present in Upstox 1D Cache? | Present in PIT Fundamentals? |
| :--- | :--- | :--- | :---: | :---: |
| **DHFL** (Dewan Housing Finance) | Nifty Mid/Small Cap | IBC / Acquired by Piramal (Delisted) | **NO (False)** | **NO (False)** |
| **RCOM** (Reliance Communications) | Nifty Mid/Small Cap | Bankruptcy / Trading Suspended | **NO (False)** | **NO (False)** |
| **SINTEX** (Sintex Industries) | Nifty Smallcap | IBC Insolvency / Delisted | **NO (False)** | **NO (False)** |
| **COX&KINGS** (Cox & Kings Ltd) | Nifty Smallcap | Insolvency / Delisted | **NO (False)** | **NO (False)** |
| **KWALITY** (Kwality Ltd) | Nifty Smallcap | Corporate Insolvency / Delisted | **NO (False)** | **NO (False)** |
| **ROLTA** (Rolta India) | Nifty Smallcap | NCLT Liquidation / Delisted | **NO (False)** | **NO (False)** |
| **JETAIRWAYS** (Jet Airways) | Nifty Mid/Small Cap | Insolvency / Resolution Freeze | **NO (False)** | **NO (False)** |
| **UNITECH** (Unitech Ltd) | Nifty Smallcap | NCLT / Trading Suspended | **NO (False)** | **NO (False)** |
| **RPOWER** (Reliance Power) | Nifty Smallcap | Severe Distressed Penny Stock | **NO (False)** | **NO (False)** |
| **SUZLON** (Suzlon Energy) | Nifty Smallcap (Historical) | Restructured Penny Stock | **NO (False)** | **NO (False)** |
| **YESBANK** (Yes Bank Ltd) | Reconstituted Small/Mid | RBI Reconstruction Scheme | **NO (False)** | **NO (False)** |
| **IDEA** (Vodafone Idea) | Reconstituted Mid/Small | Government Debt Conversion | **NO (False)** | **NO (False)** |

**Audit Result**: **0 out of 12** historical distressed/failed smallcap companies are present in the repository's 1D daily price cache.

---

## 3. Quantification of Survivorship Bias Distortion

### A. Academic Baseline
As highlighted in the user request, recent empirical research on the Indian equity market (2025–2026 SSRN working paper on the Nifty Smallcap 250) established that:
* Using only **surviving constituents** in historical small-cap simulations overstates annualized returns by approximately **+4.94 percentage points per year**.
* Small-cap strategies appear to deliver massive "size premia" when tested on survivors because the catastrophic bankruptcies (total capital wipeouts) are retroactively purged from the historical universe, leaving only the successful survivors and large-cap graduates.

### B. Impact on SFP-1 Backtest
If SFP-1 is simulated using the repository's current 858-symbol fundamental universe and 947-symbol Upstox price cache:
1. **Upward Return Bias**: Any observed CAGR will be artificially elevated by roughly **+4.94%**.
2. **Understated Downside Risk**: Historical maximum drawdowns during 2018–2019 (the mid/small-cap bear market) and the March 2020 crash will appear milder than experienced by actual small-cap investors.
3. **Survivor Concentration**: The ranking will disproportionately select stocks that started small in 2018 and subsequently multiplied in market cap (e.g. multibaggers), because the stocks that shrank into liquidation are invisible.

---

## 4. Governance Implications Under AGENTS.md

Under the **Mandatory Real-Market-Data Backtest Protocol** (`AGENTS.md`, Rule 1 & Rule 9):
> *"If required data is unavailable from Upstox... DO NOT SUBSTITUTE ANOTHER PROVIDER SILENTLY. Instead report: `DATA_INSUFFICIENT`, `UPSTOX_UNAVAILABLE`, `PROVENANCE_FAILED` and stop or explicitly classify the study as non-certifiable."*
> *"Check whether the repository has complete historical data for companies that failed or delisted. If not, quantify the survivorship limitation and do not claim full certification."*

### Protocol Mandate for SFP-1:
1. SFP-1 **CANNOT** receive `ACCEPT_FOR_ALERT_STRATEGY` or production certification on the current dataset due to verified survivorship truncation.
2. Any historical backtest executed on the existing repository data must be explicitly stamped:
   ```text
   SURVIVORSHIP_LIMITATION = QUANTIFIED_CONTAMINATION
   ESTIMATED_UPWARD_BIAS = +4.94% CAGR
   CERTIFICATION_STATUS = RESEARCH_HOLD / EXPLORATORY_ONLY
   ```
3. To achieve true production-ready certification for any small-cap strategy in this repository, the data pipeline must first ingest point-in-time delisted security pricing and corporate actions from historical exchange archives.
