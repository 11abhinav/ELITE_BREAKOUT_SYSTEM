# MOMENTUM_IGNITION_5M — DATA CERTIFICATION & PROVENANCE REPORT

**Source API:** Upstox API V3 (`https://api.upstox.com/v3/historical-candle/{key}/minutes/5/...`)  
**Data Directory:** `data/clean_upstox_5m_oi/`  
**Provenance Manifests:** `reports/clean_upstox_5m_oi_provenance.csv`, `reports/clean_upstox_5m_oi_provenance.json`  

---

## 1. Verified Invariants
1. **Contract Mapping**: 100% of underlying stocks resolve to active near-month FUTSTK contracts (`NSE_FO|...`).
2. **Regulatory Expiry Alignment**: Every contract expires on the official **NSE Last Tuesday** (effective since August 11, 2026), eliminating the legacy Thursday rollover bug.
3. **Native Open Interest**: 100% of bars extract the 7th element in the Upstox V3 candle response (`[ts, O, H, L, C, V, OI]`).
4. **Data Health Verification**:
   - Zero synthetic, simulated, or interpolated data.
   - Zero duplicate bar timestamps.
   - Zero weekend or holiday sessions.
   - Timezone strictly Asia/Kolkata (IST).
   - SHA256 cryptographic checksums generated for all 38 clean parquets.
