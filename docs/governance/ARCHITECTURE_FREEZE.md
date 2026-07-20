Document: ARCHITECTURE_FREEZE.md
Version: 1.0
Governance Version: 1.0
Status: Frozen
Parent Constitution: 1.0
Effective Date: 2026-07-20

# Architecture Freeze

## 1. Data Provider Pipeline
The system enforces a strict hierarchical provider priority to guarantee high availability while managing rate limits.
1.  **Fyers (Primary):** Used for all real-time tick and intraday data fetching. Requires automated refresh tokens and manual daily login via `/fyers/login`.
2.  **Yahoo Finance (Secondary/Fallback):** Used for EOD, historical data, and fundamentals. Handles the bulk of the offline scanning requirements.
3.  **BSE Fallback (Tertiary):** `.BO` suffix queries are triggered explicitly when NSE data fails or the symbol is exclusively listed on the BSE.

## 2. Cache Lifecycle Management
Data must never be re-fetched unnecessarily. The cache layer acts as a strict firewall.
*   **Fundamental Data:** Cached for 15 days in PostgreSQL (`fundamentals_cache` table). Must NEVER be re-fetched within this window unless forced.
*   **EOD Price Data:** Cached daily. Invalidation occurs at 00:00 IST.
*   **Intraday Price Data:** Cached using a Redis-like mechanism or DB fast-path. Cache duration is linked to the scanner execution frequency (e.g., 5m, 15m, 60m).
*   **Invalidation Policy:** Invalid data MUST NOT overwrite higher-quality cache data. Dead symbols must remain in the cache until natural expiry.

## 3. Database Engine & Contracts
*   **Persistent State:** PostgreSQL is the singular source of truth for the system.
*   **Ephemeral Nature:** The system is deployed on Railway (ephemeral disk). No local JSON or SQLite files are permitted for persistent storage.
*   **JSONB Usage:** Payloads stored in JSONB columns must be explicitly sanitized (e.g., stripping out `NaN`, `Infinity`) before insertion to prevent `InvalidTextRepresentation` exceptions.

## 4. State Transitions (The Pipeline)
Alerts follow a strict, unidirectional state machine:
1.  `SETUP_ARMED`: Stock passes fundamental gates and forms a preliminary setup.
2.  `ENTRY_READY`: Stock breaches technical thresholds (e.g., resistance, moving averages).
3.  `TRADE_ACTIVE`: Order executed; trailing stop-loss logic engages.

No stock may enter `TRADE_ACTIVE` without explicitly passing through the preceding states and surviving the validation gates.
