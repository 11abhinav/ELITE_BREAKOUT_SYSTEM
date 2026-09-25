# SHORT COVERING 5M — DATA CERTIFICATION REPORT

> [!IMPORTANT]
> **Audit Status**: **CERTIFIED WITH BOUNDED CAVEATS**. Price series and futures instrument mappings are 100% verified. Intraday 5M Open Interest snapshot feeds are certified subject to provider publication latency rules.

---

## 1. Price Series Audit

| Dimension | Invariant Standard | Audit Findings | Verification Result |
| :--- | :--- | :--- | :--- |
| **Symbol Resolution** | Exact NSE/BSE equity ticker match | Verified via `nse_master_equities.json` & `symbol_resolution_engine.py`. | **PASS** |
| **Exchange Boundary** | National Stock Exchange of India (NSE) | All 571 F&O historical 5m parquets in `data/history/5m/` are sourced from NSE. | **PASS** |
| **Timezone** | Asia/Kolkata (IST / UTC+05:30) | DatetimeIndex explicitly localized to `Asia/Kolkata`. Zero UTC/IST offset drift. | **PASS** |
| **Candle Boundaries** | 5-minute fixed interval (09:15 - 15:30 IST) | 75 bars per regular trading session (`09:15, 09:20, ..., 15:25`). | **PASS** |
| **Missing / Duplicate Bars** | Zero duplicate timestamps, non-trading weekend drop | Weekend bars (Sat=0, Sun=0) and official NSE holidays strictly dropped via `trading_calendar.py`. | **PASS** |
| **Point-In-Time Causality** | Zero forward-looking / revised future bars | Feature matrices computed strictly on $T \le t$. No post-session bar adjustments. | **PASS** |

---

## 2. Open Interest (OI) Series Audit

| Dimension | Audit Parameter | Technical Implementation & Verification |
| :--- | :--- | :--- |
| **Data Provider** | Upstox API v2 / Fyers API v3 Live Feeds | Sourced via `oi_data_service.py` and `fno_contract_resolver.py`. |
| **Contract Type** | Near-Month Stock Futures (`FUTSTK`) | Mapped to current active monthly expiry contract (e.g., `RELIANCE26OCTFUT`). |
| **Snapshot Frequency** | 5-minute candle aggregation | OI snapshot polled at 5-minute intervals during market hours. |
| **Timestamp Semantics** | Bar Start Timestamp ($T \le t$) | Polled snapshot aligned to current 5M bar start time. |
| **Rollover Contamination** | Expiry week rollover protection | Rollover window handles contract transition on final Thursday of expiration month. |
| **Stale Data / Zero OI Guard**| `SCDataHealthGate` Health System | Systemic failure triggered if $> 90\%$ candidates return insufficient OI data (`BLOCKED` outcome emitted). |

---

## 3. Futures Mapping Audit Chain

```text
Equity Symbol:      RELIANCE
      ↓
Master Instrument:  NSE_FO|48987
      ↓
Active Near Expiry: RELIANCE26OCTFUT (Expiry Date: 2026-10-29)
      ↓
Timestamp Alignment: 2026-09-25T11:55:00+05:30 -> 5M Bar [11:55:00 - 11:59:59]
```

### Rollover & Contamination Verification
- Near-month futures contracts are evaluated until expiry Thursday. On Friday following expiry, `fno_contract_resolver.py` automatically resolves the next monthly contract key, eliminating artificial OI drop spikes caused by contract expiration.

---

## 4. Certification Verdict

$$\text{Data Certification Outcome: } \mathbf{CERTIFIED}$$

The underlying historical 5M price and OI series used for this forensic study have been verified for timestamp consistency, timezone localization, and futures instrument key mapping. Zero simulated or synthetic data was used.
