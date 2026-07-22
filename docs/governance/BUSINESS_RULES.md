Document: BUSINESS_RULES.md
Version: 1.0
Governance Version: 1.0
Status: Frozen
Parent Constitution: 1.0
Effective Date: 2026-07-20

# Business Rules Freeze (The Registry)

This document is the authoritative specification for all business logic decisions made by the Elite Breakout System. The implementation (code) MUST conform to these rules. Code and Test files DO NOT live here; they are discovered dynamically via the `traceability_report.md` generated in CI.

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
*   **Description:** A valid breakout candidate MUST exhibit trading volume strictly greater than 1.5 times its 20-day Simple Moving Average (SMA) of volume.
*   **Lifecycle:** Frozen
*   **Required Verification Types:** Unit Test, Golden Dataset

### [EOD-002] Minimum Quality Threshold (Core Bucket)
*   **Description:** A stock classified in the 'Core' portfolio bucket MUST have a Return on Capital Employed (ROCE) >= 20%. Failing this places the stock in the "REVIEW" category.
*   **Lifecycle:** Frozen
*   **Required Verification Types:** Unit Test

## 2. Multi-Timeframe (MTF) Rules

### [MTF-001] Hourly RSI Confirmation
*   **Description:** For an intraday breakout to be confirmed, the hourly candle must close above the trigger price AND the hourly RSI must be strictly greater than 60.
*   **Lifecycle:** Frozen
*   **Required Verification Types:** Unit Test, Invariant

## 3. Reversal Rules

### [REV-001] Reversal Structural Prerequisites
*   **Description:** A valid reversal setup must occur after a minimum 15% drawdown from the 52-week high, and the daily MACD histogram must be positive (tick higher than previous day).
*   **Lifecycle:** Frozen
*   **Required Verification Types:** Unit Test, Golden Dataset

## 4. Stop-Loss & Target Rules

### [SL-001] Initial Stop Loss Baseline
*   **Description:** The `initial_stop_loss` is the immutable source of truth generated at the time of the alert. It must never be destructively overwritten by trailing stop loss logic.
*   **Lifecycle:** Frozen
*   **Required Verification Types:** Unit Test, Invariant

### [SL-002] Trailing Mechanism
*   **Description:** The trailing stop loss (`stop_loss`) must trail 3 Average True Range (ATR) periods below the most recent swing low, once the stock achieves Target 1.
*   **Lifecycle:** Frozen
*   **Required Verification Types:** Unit Test

## 5. Junk-Gate & Data Integrity Rules

### [VAL-001] Missing Data Veto
*   **Description:** If a fundamental fetch returns empty values for both Financials and Balance Sheet, the fetch MUST raise a `ValueError` and the stock must be marked `is_invalidated = True`. Silent progression is prohibited.
*   **Lifecycle:** Frozen
*   **Required Verification Types:** Unit Test, Invariant

## 6. System Performance & Memory Rules

### [SYS-001] Strict Performance Over Memory Policy
*   **Description:** Memory saving and garbage collection optimizations MUST NEVER compromise or degrade performance, data accuracy, or scan results. Any memory tracker or profiler must correctly scope data cleanup *after* full processing logic is evaluated to prevent premature purges. This is a non-negotiable rule.
*   **Lifecycle:** Frozen
*   **Required Verification Types:** Unit Test, Invariant
