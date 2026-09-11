# V5.26 Live Shadow Telemetry & Manual Evaluation Dashboard

**Configuration Version**: `V5.26_SHADOW`  
**Generated At**: `2026-09-11T12:36:58.442547`  
**Source Telemetry Database**: `data/shadow_telemetry.db`  

---

## 1. Executive Shadow Comparison Summary
- Total Live Candidates Audited: `154`
- Old Legacy Status (Arm A): Blind Gem Carry applied to all morning alerts.
- V5.26 Shadow Status (Arm C/D): Deterministic Catalyst State Routing with strict <=60m Intraday TTL and structural revalidation.

---

## 2. Sample Telemetry Breakdown (Auditable Alert Changes)

| Scanner | Symbol | Decision Time | Gem Age | Catalyst State | CLV | Ext (R) | Vol | Old Rank | New Rank | Shadow Status | Decision Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **MultiTF 1H** | `RELIANCE` | `10:15:00` | `15m` | **`LIVE_GEM_ACTIVE`** | `0.67` | `0.9R` | `1.1x` | `#59` | `#1` | **`SELECTED`** | Live Intraday Gem Active (Age 15.0m <= 60m TTL) |
| **MultiTF 1H** | `TCS` | `10:15:00` | `N/A` | **`ORGANIC_INTRADAY`** | `0.85` | `1.0R` | `1.3x` | `#132` | `#55` | **`FILTERED`** | Organic Intraday Candidate (No Gem) |
| **MultiTF 1H** | `INFY` | `10:15:00` | `55m` | **`LIVE_GEM_ACTIVE`** | `0.18` | `-1.2R` | `2.0x` | `#88` | `#2` | **`SELECTED`** | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| **MultiTF 1H** | `HDFCBANK` | `10:15:00` | `30m` | **`LIVE_GEM_ACTIVE`** | `0.90` | `1.6R` | `2.5x` | `#1` | `#3` | **`SELECTED`** | Live Intraday Gem Active (Age 30.0m <= 60m TTL) |
| **MultiTF 1H** | `ICICIBANK` | `10:15:00` | `30m` | **`LIVE_GEM_ACTIVE`** | `0.90` | `1.6R` | `2.5x` | `#2` | `#4` | **`SELECTED`** | Live Intraday Gem Active (Age 30.0m <= 60m TTL) |
| **MultiTF 1H** | `SBIN` | `10:15:00` | `55m` | **`LIVE_GEM_ACTIVE`** | `0.18` | `-1.2R` | `2.0x` | `#89` | `#5` | **`SELECTED`** | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| **MultiTF 1H** | `BHARTIARTL` | `10:15:00` | `55m` | **`LIVE_GEM_ACTIVE`** | `0.18` | `-1.2R` | `2.0x` | `#90` | `#6` | **`FILTERED`** | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| **MultiTF 1H** | `TATAMOTORS` | `10:15:00` | `55m` | **`LIVE_GEM_ACTIVE`** | `0.86` | `3.6R` | `1.8x` | `#37` | `#7` | **`FILTERED`** | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| **MultiTF 1H** | `LTIM` | `10:15:00` | `30m` | **`LIVE_GEM_ACTIVE`** | `0.90` | `1.6R` | `2.5x` | `#3` | `#8` | **`FILTERED`** | Live Intraday Gem Active (Age 30.0m <= 60m TTL) |
| **MultiTF 1H** | `DIXON` | `10:15:00` | `15m` | **`LIVE_GEM_ACTIVE`** | `0.67` | `0.9R` | `1.1x` | `#60` | `#9` | **`FILTERED`** | Live Intraday Gem Active (Age 15.0m <= 60m TTL) |
| **MultiTF 1H** | `POLYCAB` | `10:15:00` | `30m` | **`LIVE_GEM_ACTIVE`** | `0.90` | `1.6R` | `2.5x` | `#4` | `#10` | **`FILTERED`** | Live Intraday Gem Active (Age 30.0m <= 60m TTL) |
| **MultiTF 1H** | `KALYANKJIL` | `10:15:00` | `30m` | **`LIVE_GEM_ACTIVE`** | `0.90` | `1.6R` | `2.5x` | `#5` | `#11` | **`FILTERED`** | Live Intraday Gem Active (Age 30.0m <= 60m TTL) |
| **MultiTF 1H** | `TRENT` | `10:15:00` | `50m` | **`LIVE_GEM_ACTIVE`** | `0.14` | `-1.0R` | `1.2x` | `#106` | `#12` | **`FILTERED`** | Live Intraday Gem Active (Age 50.0m <= 60m TTL) |
| **MultiTF 1H** | `ZOMATO` | `10:15:00` | `15m` | **`LIVE_GEM_ACTIVE`** | `0.67` | `0.9R` | `1.1x` | `#61` | `#13` | **`FILTERED`** | Live Intraday Gem Active (Age 15.0m <= 60m TTL) |
| **MultiTF 5M** | `RELIANCE` | `11:30:00` | `105m` | **`INTRADAY_EXPIRED`** | `0.90` | `1.6R` | `2.5x` | `#6` | `#92` | **`FILTERED`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| **MultiTF 5M** | `TCS` | `11:30:00` | `N/A` | **`ORGANIC_INTRADAY`** | `0.85` | `1.0R` | `1.3x` | `#133` | `#56` | **`FILTERED`** | Organic Intraday Candidate (No Gem) |
| **MultiTF 5M** | `INFY` | `11:30:00` | `105m` | **`INTRADAY_EXPIRED`** | `0.90` | `1.6R` | `2.5x` | `#7` | `#93` | **`FILTERED`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| **MultiTF 5M** | `HDFCBANK` | `11:30:00` | `125m` | **`INTRADAY_EXPIRED`** | `0.14` | `-1.0R` | `1.2x` | `#107` | `#94` | **`FILTERED`** | Intraday Gem Expired (Age 125.0m > 60m TTL) |
| **MultiTF 5M** | `ICICIBANK` | `11:30:00` | `130m` | **`INTRADAY_EXPIRED`** | `0.86` | `3.6R` | `1.8x` | `#38` | `#95` | **`FILTERED`** | Intraday Gem Expired (Age 130.0m > 60m TTL) |
| **MultiTF 5M** | `SBIN` | `11:30:00` | `130m` | **`INTRADAY_EXPIRED`** | `0.18` | `-1.2R` | `2.0x` | `#91` | `#96` | **`FILTERED`** | Intraday Gem Expired (Age 130.0m > 60m TTL) |
| **MultiTF 5M** | `BHARTIARTL` | `11:30:00` | `130m` | **`INTRADAY_EXPIRED`** | `0.86` | `3.6R` | `1.8x` | `#39` | `#97` | **`FILTERED`** | Intraday Gem Expired (Age 130.0m > 60m TTL) |
| **MultiTF 5M** | `TATAMOTORS` | `11:30:00` | `125m` | **`INTRADAY_EXPIRED`** | `0.14` | `-1.0R` | `1.2x` | `#108` | `#98` | **`FILTERED`** | Intraday Gem Expired (Age 125.0m > 60m TTL) |
| **MultiTF 5M** | `LTIM` | `11:30:00` | `130m` | **`INTRADAY_EXPIRED`** | `0.86` | `3.6R` | `1.8x` | `#40` | `#99` | **`FILTERED`** | Intraday Gem Expired (Age 130.0m > 60m TTL) |
| **MultiTF 5M** | `DIXON` | `11:30:00` | `105m` | **`INTRADAY_EXPIRED`** | `0.90` | `1.6R` | `2.5x` | `#8` | `#100` | **`FILTERED`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| **MultiTF 5M** | `POLYCAB` | `11:30:00` | `130m` | **`INTRADAY_EXPIRED`** | `0.86` | `3.6R` | `1.8x` | `#41` | `#101` | **`FILTERED`** | Intraday Gem Expired (Age 130.0m > 60m TTL) |

---

## 3. Catalyst State Breakdown in Shadow

| Catalyst State | Candidate Count | Avg Old Rank | Avg New Rank | Sizing (R) | Production Action |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`CATALYST_SURVIVED`** | High-Quality Consolidation | Promoted | Top Tier | **1.00R** | 🟢 Priority 1 Continuation Allocation |
| **`FRESH_BASE`** | Clean EOD Bases | Promoted | Top Tier | **1.00R** | 🟢 Priority 1 Organic Allocation |
| **`LIVE_GEM_ACTIVE`** | Intraday <= 60m TTL | Promoted | Top Tier | **1.00R** | 🟢 Live Intraday Momentum Surge |
| **`MORNING_TRAP_ACTIVE`**| Short Covering Specialist | Demoted in Longs | Sized 1.50R | ⚡ **1.50R Inverse Hedge Allocation** |
| **`CATALYST_COOLING`** | Moderate Structure | Maintained | Mid Tier | **0.75R** | 🟡 Controlled Standard Revenue |
| **`CATALYST_EXHAUSTED`**| Climax Runners (>3.2R) | Ranked Top in Arm A | **Demoted / VETO** | **0.00R** | 🔴 **VETOED: Zero Stale Climax Drag** |
| **`CATALYST_INVALIDATED`**| Structure Breakdown (<VWAP)| Ranked Mid in Arm A | **Demoted / VETO** | **0.00R** | 🔴 **VETOED: Hard Breakdown Rejection** |

---

## 4. Operational Invariant Verification
- [x] **Zero Weekend Bars**: Saturday/Sunday filtering strictly enforced.
- [x] **Zero Lookahead**: Decision timestamp <= entry timestamp verified.
- [x] **Immutable Parameter Registry**: DB records linked to `V5.26_SHADOW`.
- [x] **Human-Auditable Rationale**: Every state transition logged.