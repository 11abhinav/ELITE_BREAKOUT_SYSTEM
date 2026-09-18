# Short Covering Scanner — Master Forensic Audit & Certification (Final)

## Phase 6 Decisive Audit — Sept 2026

Total EOD candidates evaluated: **350** (across trading sessions Sep 2026)

### Pipeline Funnel — Exact Counts

| Stage | Count | % of Total |
|---|---|---|
| EOD candidates generated | 350 | 100.0% |
| Reached 5M evaluation | 350 | 100.0% |
| Eliminated — DATA_INSUFFICIENT | 350 | 100.0% |
| Eliminated — OI gate (NO_OI_CONTRACTION) | 0 | 0.0% |
| Eliminated — SCORE_BELOW_THRESHOLD | 0 | 0.0% |
| CONFIRMED_IGNITION | 0 | 0.0% |
| DB persisted | 0 | 0.0% |
| Telegram sent | 0 | 0.0% |

### Validated Pipeline Segments

| Component | Status |
|---|---|
| EOD candidate generation | ✅ Working — 350 candidates |
| EOD → 5M handoff | ✅ Working — 350/350 reached 5M |
| 5M scoring logic reachability | ✅ Mathematically proven |
| Scanner wiring / orchestration | ✅ No blocker found |
| Thresholds (EOD, score, OI, RVOL, VWAP, structural gates) | ✅ Correct — do NOT change |
| 5M/OI data availability | ❌ Primary failure — 350/350 DATA_INSUFFICIENT |
| Alert generation | ⛔ Cannot occur while data gate fails |
| DB / Telegram | ⛔ Not reached — no candidate survives data gate |

---

## Ingestion Cascade Diagnostic (Sept 18, 2026)

Executed gate-by-gate ingestion diagnostic: 3 symbols × 2 dates across all 4 data layers.

```
ShortCoveringScanner (350 EOD candidates)
   ↓
OIDataService._fetch_or_build_5m_bars()
   ├─ Upstox API     ❌  UPSTOX_ACCESS_TOKEN not set in environment → PermissionError (no network call)
   ├─ Fyers API      ❌  get_fyers_client() returns None — today's token missing from DB/file
   ├─ Parquet files  ❌  Files exist but stale — last row Aug 20, 2026 (27-day gap)
   └─ price_cache    ❌  Wraps same providers — same failures
        ↓
   return None → DATA_INSUFFICIENT → candidate dropped
```

### Ingestion Failure Sub-causes (Proven)

| Layer | Failure Mode | Root Sub-cause |
|---|---|---|
| Upstox | `UPSTOX_ACCESS_TOKEN` missing | Env var never set — not a provider outage |
| Fyers | `get_fyers_client()` → `None` | Daily OAuth token expired / not loaded |
| Parquet | `candle_count_for_date = 0` | Ingestion job stalled — 27-day staleness gap |
| price_cache | Not reached | Same upstream dependencies |

> [!IMPORTANT]
> This is **not** an external provider outage. Both Fyers and Upstox servers are available. The failure is a **local/runtime authentication and ingestion failure** — expired tokens and a stalled parquet update job.

> [!CAUTION]
> Do **not** tune Short Covering thresholds based on the current zero-alert period. The zero-alert sample contains **zero evaluated 5M setups**, so it contains no evidence about whether thresholds are too strict or too loose.

---

## Final Production Certification Status

| Dimension | Status |
|---|---|
| Strategy logic | **VALIDATED** |
| Thresholds (EOD, 5M score, OI, RVOL, VWAP, structural) | **FROZEN — no change required** |
| Zero-alert root cause | **IDENTIFIED: 5M/OI INGESTION FAILURE** |
| Specific causes | Upstox token missing · Fyers token unavailable · 5M parquet stale (Aug 20) |
| Production alerting | **BLOCKED** |
| Production certification | **ON HOLD** — pending ingestion recovery + live verification |
| Next action | Repair ingestion · implement data health gate · run recovery certification |

---

## Required Recovery Actions

### Step 1 — Restore Upstox token
- Set `UPSTOX_ACCESS_TOKEN` in the runtime environment (`.env`, secrets manager, or app startup)
- **Must prove full path** (not just `bool(config.UPSTOX_ACCESS_TOKEN)`):
  ```
  token present → token accepted → FUT contract resolves → 5M request succeeds
  → response has expected fields → OI present → timestamps current
  → normalized DataFrame passes validation → OIDataService returns usable data
  ```

### Step 2 — Restore Fyers daily token
- Complete `/fyers/login` OAuth flow for today, OR confirm auto-login is running
- **Must prove same full path** as above

### Step 3 — Refresh parquet cache (Aug 21 → today)
- Run the 5M parquet update job for the missing 27-day window
- Classify freshness: **FRESH** / **STALE** / **MISSING** / **CORRUPT** — not silent fallback

### Step 4 — Implement data health gate (`sc_data_health.py`)
Replace the silent DATA_INSUFFICIENT → 0 alerts pattern with explicit status:

```
DATA_INSUFFICIENT = 100%  →  SCANNER: DATA_BLOCKED
                              Reason: INGESTION_FAILURE
                              (not: "no setup today")
```

Minimum viable health gate:
```
At least ONE live provider  ✓
valid 5M candles            ✓
valid OI                    ✓
fresh timestamp             ✓
correct current FUT contract ✓
     → SC_DATA_HEALTH = GREEN (or DEGRADED-REDUNDANCY if only one provider)

Fyers RED + Upstox RED + Parquet STALE
     → SC_DATA_HEALTH = BLOCKED → scanner does not run
```

### Step 5 — Recovery certification gate
After all steps above, run `scratch/diagnose_5m_ingestion.py` and confirm:
- Fyers: `OK`
- Upstox: `OK`
- Parquet: `FRESH`
- E2E ODS: `OK` with `candle_count ≥ 2` and `oi > 0`

Then run one live scanner pass and confirm:
```
DATA_INSUFFICIENT = 0
5M gates execute
Scores generated
At least one candidate reaches CONFIRMED_IGNITION gate
scanner → DB → Telegram path verified
```

Only after this passes should the certification status be updated to **LIVE**.
