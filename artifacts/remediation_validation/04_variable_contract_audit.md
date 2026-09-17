# 04 — Variable Contract & Lifecycle Audit
Generated: 2026-09-17 18:33:35 IST

## New Fields Introduced
1. `oi_data_mode`: `DERIVATIVE_OI_AVAILABLE` | `DERIVATIVE_OI_UNAVAILABLE`
2. `oi_delta_1bar`: float or NaN
3. `oi_delta_3bar`: `(OI[t] - OI[t-3]) / OI[t-3]` (float or NaN)
4. `oi_session`: float or NaN
5. `eod_alert_today`: boolean (metadata only)
6. `extension_from_vwap`: float
7. `extension_from_low`: float

## Lifecycle & Initialization Guarantee
- Every variable is explicitly initialized before branch execution.
- NaN and None states are handled safely without downstream type errors.
- Status: **`PASS`**
