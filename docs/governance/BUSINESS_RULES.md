Document: BUSINESS_RULES.md
Version: 1.0
Governance Version: 1.0
Status: Frozen
Parent Constitution: 1.0
Effective Date: 2026-07-20

# Business Rules Freeze (The Registry)

This document is the authoritative specification for all business logic decisions made by the Elite Breakout System. The implementation (code) MUST conform to these rules, and they MUST be verified by a corresponding test or golden snapshot.

## Rule Lifecycle States
*   **Draft**: Rule is being formulated.
*   **Proposed**: Rule is pending Change Request approval.
*   **Approved**: Rule is approved but not yet deployed.
*   **Frozen**: Rule is active in production.
*   **Deprecated**: Rule is slated for removal.
*   **Retired**: Rule is no longer active, but retained for historical auditability.

---

## 1. End-Of-Day (EOD) Breakout Rules

### [EOD-001] Minimum Volume Breakout
*   **Status:** Frozen
*   **Specification:** A valid breakout candidate MUST exhibit trading volume strictly greater than 1.5 times its 20-day Simple Moving Average (SMA) of volume.
*   **Verification:** `test_eod_rule_001.py` & `snapshots/eod_healthy_volume.json`

### [EOD-002] Minimum Quality Threshold (Core Bucket)
*   **Status:** Frozen
*   **Specification:** A stock classified in the 'Core' portfolio bucket MUST have a Return on Capital Employed (ROCE) >= 20%. Failing this places the stock in the "REVIEW" category.
*   **Verification:** `test_eod_rule_002.py`

## 2. Multi-Timeframe (MTF) Rules

### [MTF-001] Hourly RSI Confirmation
*   **Status:** Frozen
*   **Specification:** For an intraday breakout to be confirmed, the hourly candle must close above the trigger price AND the hourly RSI must be strictly greater than 60.

## 3. Reversal Rules

### [REV-001] Reversal Structural Prerequisites
*   **Status:** Frozen
*   **Specification:** A valid reversal setup must occur after a minimum 15% drawdown from the 52-week high, and the daily MACD histogram must be positive (tick higher than previous day).

## 4. Stop-Loss & Target Rules

### [SL-001] Initial Stop Loss Baseline
*   **Status:** Frozen
*   **Specification:** The `initial_stop_loss` is the immutable source of truth generated at the time of the alert. It must never be destructively overwritten by trailing stop loss logic.

### [SL-002] Trailing Mechanism
*   **Status:** Frozen
*   **Specification:** The trailing stop loss (`stop_loss`) must trail 3 Average True Range (ATR) periods below the most recent swing low, once the stock achieves Target 1.

## 5. Junk-Gate & Data Integrity Rules

### [VAL-001] Missing Data Veto
*   **Status:** Frozen
*   **Specification:** If a fundamental fetch returns empty values for both Financials and Balance Sheet, the fetch MUST raise a `ValueError` and the stock must be marked `is_invalidated = True`. Silent progression is prohibited.
