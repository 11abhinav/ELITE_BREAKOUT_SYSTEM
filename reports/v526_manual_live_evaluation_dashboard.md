# V5.26 Live Shadow Telemetry & Manual Evaluation Dashboard

**Configuration Version**: `V5.26_SHADOW`  
**Generated At**: `2026-09-11T12:46:42.428220`  
**Source Telemetry Database**: `data/shadow_telemetry.db`  

---

## 1. Executive Daily Scanner Comparison

| Scanner | Legacy A Alerts | V5.26 Shadow Alerts | Net Diff (Δ) | Avoided Trades (Climax/Stale) | New Trades (Fresh/Survived) | Unchanged Trades |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **MultiTF 1H** | `8` | `10` | `+2` | 🔴 **`4`** | 🟢 **`6`** | ⚪ **`4`** |
| **MultiTF 5M** | `2` | `0` | `-2` | 🔴 **`2`** | 🟢 **`0`** | ⚪ **`0`** |
| **Short Covering** | `0` | `0` | `0` | 🔴 **`0`** | 🟢 **`0`** | ⚪ **`0`** |
| **Daily Builder** | `0` | `0` | `0` | 🔴 **`0`** | 🟢 **`0`** | ⚪ **`0`** |
| **Reversal** | `0` | `0` | `0` | 🔴 **`0`** | 🟢 **`0`** | ⚪ **`0`** |
| **Pullback V2** | `0` | `0` | `0` | 🔴 **`0`** | 🟢 **`0`** | ⚪ **`0`** |
| **Multibagger** | `0` | `0` | `0` | 🔴 **`0`** | 🟢 **`0`** | ⚪ **`0`** |
| **EOD Breakout** | `0` | `0` | `0` | 🔴 **`0`** | 🟢 **`0`** | ⚪ **`0`** |
| **Accumulation VCP** | `0` | `0` | `0` | 🔴 **`0`** | 🟢 **`0`** | ⚪ **`0`** |
| **Wealth Engine** | `0` | `0` | `0` | 🔴 **`0`** | 🟢 **`0`** | ⚪ **`0`** |
| **Technical Ahat** | `0` | `0` | `0` | 🔴 **`0`** | 🟢 **`0`** | ⚪ **`0`** |

---

## 2. Signal Disagreement Log (The Manual Review Heart)

### A. Avoided Trades (Suppressed Stale Climax / Invalidated Breakdown)
These are candidates the Legacy system would have promoted, but V5.26 suppressed to prevent stale climax drag:

| Timestamp | Scanner | Symbol | Old Rank | New Rank | Catalyst State | Gem Age | CLV | Extension | Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `10:15:00` | **MultiTF 1H** | `LTIM` | `#3` | `#8` | **`LIVE_GEM_ACTIVE`** | `30m` | `0.90` | `1.6R` | Live Intraday Gem Active (Age 30.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `POLYCAB` | `#4` | `#10` | **`LIVE_GEM_ACTIVE`** | `30m` | `0.90` | `1.6R` | Live Intraday Gem Active (Age 30.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `KALYANKJIL` | `#5` | `#11` | **`LIVE_GEM_ACTIVE`** | `30m` | `0.90` | `1.6R` | Live Intraday Gem Active (Age 30.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `BHARTIARTL` | `#3` | `#7` | **`LIVE_GEM_ACTIVE`** | `30m` | `0.90` | `1.6R` | Live Intraday Gem Active (Age 30.0m <= 60m TTL) |
| `11:30:00` | **MultiTF 5M** | `RELIANCE` | `#4` | `#89` | **`INTRADAY_EXPIRED`** | `105m` | `0.90` | `1.6R` | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| `11:30:00` | **MultiTF 5M** | `KALYANKJIL` | `#5` | `#99` | **`INTRADAY_EXPIRED`** | `105m` | `0.90` | `1.6R` | Intraday Gem Expired (Age 105.0m > 60m TTL) |

### B. New Promoted Trades (Fresh EOD Bases & Survived Catalysts)
These are high-quality consolidation structures or surviving catalysts elevated by V5.26:

| Timestamp | Scanner | Symbol | Old Rank | New Rank | Catalyst State | Gem Age | CLV | Runway | Sizing | Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `10:15:00` | **MultiTF 1H** | `RELIANCE` | `#59` | `#1` | **`LIVE_GEM_ACTIVE`** | `15m` | `0.67` | `2.3 ATR` | `1.00R` | Live Intraday Gem Active (Age 15.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `INFY` | `#88` | `#2` | **`LIVE_GEM_ACTIVE`** | `55m` | `0.18` | `4.2 ATR` | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `SBIN` | `#89` | `#5` | **`LIVE_GEM_ACTIVE`** | `55m` | `0.18` | `4.2 ATR` | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `RELIANCE` | `#36` | `#1` | **`LIVE_GEM_ACTIVE`** | `55m` | `0.86` | `0.4 ATR` | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `TCS` | `#84` | `#2` | **`LIVE_GEM_ACTIVE`** | `15m` | `0.67` | `2.3 ATR` | `1.00R` | Live Intraday Gem Active (Age 15.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `HDFCBANK` | `#33` | `#4` | **`LIVE_GEM_ACTIVE`** | `55m` | `0.86` | `0.4 ATR` | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |

---

## 3. False Veto Audit Tracker
Mandatory manual checkpoint: Monitor all `CATALYST_EXHAUSTED` and `CATALYST_INVALIDATED` signals after trade resolution to ensure no false negative structural rejection of genuine high-momentum leaders.

| Telemetry ID | Symbol | Scanner | Catalyst State | Tracked Outcome Actual R | MFE (R) | MAE (R) | Post-Trade Review Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `#TEL-001` | Pending Live Exit | Daily Builder | `CATALYST_EXHAUSTED` | `TBD` | `TBD` | `TBD` | ⏳ Awaiting Live Session Close |
| `#TEL-002` | Pending Live Exit | Reversal | `CATALYST_INVALIDATED` | `TBD` | `TBD` | `TBD` | ⏳ Awaiting Live Session Close |

---

## 4. Production Operational Status
- Current Active Production: **`V5.25_PRODUCTION`** (Unmodified)
- Parallel Shadow Observer: **`V5.26_SHADOW`** (Active in Background)
- Automatic Promotion: ❌ **DISABLED** (Manual Live Confirmation Required)