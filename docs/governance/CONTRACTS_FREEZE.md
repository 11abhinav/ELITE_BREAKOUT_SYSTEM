Document: CONTRACTS_FREEZE.md
Version: 1.0
Governance Version: 1.0
Status: Frozen
Parent Constitution: 1.0
Effective Date: 2026-07-20

# Contracts Freeze

This document freezes the interfaces, public APIs, and schema structures of the Elite Breakout System. Changing an interface often has a much wider impact than changing a threshold, and thus requires explicit Level 4 Change Request approval.

## 1. Public APIs & Endpoints
The following endpoints and their HTTP verb signatures are frozen:
*   `GET /api/messages?user={str}&t={int}`: Returns a JSON array of messages. Must never return a 404 for an unfound user; must return `[]` (HTTP 200).
*   `POST /api/messages`: Accepts `{"user": str, "message": str, "is_from_admin": bool}`.
*   `POST /api/messages/read`: Accepts `{"user": str, "as_admin": bool}`. Must return `{"status": "success"}` on user not found.
*   `GET /api/viewers`: Returns `{"active_count": int, "viewers": list, "history": list, "detailed_online": list, "unread_messages": dict}`.
*   *(Additional API definitions are tracked here)*

## 2. Core Function Signatures
The critical pipeline functions are mathematically and structurally frozen:
*   `compute_sl_and_target(entry_price, ...)` -> `Dict[str, float]`
    *   **Contract:** Must return exactly the 13 defined SL/Target fields (e.g., `target_1`, `stop_loss`, `trailing_sl`).
*   `validate_bhavcopy(...)` -> `bool`
    *   **Contract:** True means valid, False means completely rejected. Must produce a validation rejection report on False.

## 3. Database Schema
Any alteration to the PostgreSQL schema requires a Level 4 Architecture approval.
*   `alerts_status` CHECK constraints MUST align exactly with the state machine enumerations in `performance_tracker.py`.
*   `created_at` columns MUST be of type `TIMESTAMPTZ`, never `TEXT`.
*   All payload columns (like `context` or `bayesian_weights`) MUST be of type `JSONB`.

## 4. JSON Formats & Serialization
*   **NaN/Infinity Sanitization:** No standard `NaN`, `Infinity`, or `-Infinity` float values are permitted to be directly serialized into JSON strings destined for PostgreSQL. They must be explicitly sanitized to `None` using the `sanitize()` helper function.
*   **Golden Dataset Payload Schema:** The expected output schema for the validation pipeline must adhere to the exact format captured in `tests/snapshots/`.
