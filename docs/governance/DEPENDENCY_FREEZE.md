Document: DEPENDENCY_FREEZE.md
Version: 1.0
Governance Version: 1.0
Status: Frozen
Parent Constitution: 1.0
Effective Date: 2026-07-20

# External Dependencies Freeze

This document freezes the approved versions and compatibility assumptions for external libraries and data providers. A dependency upgrade or provider contract change that alters behavior must be treated as a governed Level 3/4 change.

## 1. Approved Library Versions
The following core libraries are frozen at specific versions/ranges to guarantee mathematical and behavioral stability:
*   `python`: 3.9.x
*   `pandas`: >=1.5.0, <2.0.0
*   `numpy`: >=1.23.0, <1.24.0
*   `yfinance`: Fixed at currently installed version (updates require validation against Golden Datasets).
*   `psycopg2-binary`: >=2.9.0
*   `fyers-apiv3`: Approved major version 3.

## 2. Provider Contracts & Assumptions
The system makes the following immutable assumptions about external provider payloads:

### Yahoo Finance (yfinance)
*   **Timezones:** Data returned may be timezone-naive. The system MUST explicitly cast and localize all timestamps to `Asia/Kolkata` at the fetch boundary.
*   **Symbol Suffixes:** NSE symbols must have the `.NS` suffix. BSE symbols must have the `.BO` suffix.
*   **Data Types:** `NaN` is expected for missing values; empty strings (`""`) may also occur. Both must be defensively guarded against during float casting.

### Fyers API
*   **Error Codes:** Fyers may return text-based error messages (e.g., `"authenticate"`). The system relies on string matching, not just numerical error codes.
*   **Rate Limits:** 1.5 requests per second. The system enforces dynamic timeouts mathematically calculated based on batch size to prevent ThreadPool deadlock.

## 3. Deployment Environment
*   **Cloud Platform:** Railway
*   **Ephemeral Disk Warning:** The local disk is ephemeral. No persistent configurations or mapping JSON files may be stored on disk. All state must persist in PostgreSQL.
