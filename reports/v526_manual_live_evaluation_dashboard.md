# V5.26 Live Shadow Telemetry & Manual Evaluation Dashboard

**Configuration Version**: `V5.26_SHADOW`  
**Generated At**: `2026-09-11T12:51:16.827566`  
**Source Telemetry Database**: `data/shadow_telemetry.db`  

---

## 1. Executive 4-Way Outcome Scorecard

| Four-Way Classification | Outcome Meaning | Trade Count | R Impact / Verdict |
| :--- | :--- | :--- | :--- |
| **✅ Correct Avoid** | Legacy would trade & lose; V5.26 suppressed | **`80`** | 🟢 **Capital Preserved (Eliminated Stale Climax Drag)** |
| **❌ False Avoid** | Legacy would trade & win; V5.26 suppressed | **`69`** | 🔴 **Opportunity Cost (Structural Filter False Veto)** |
| **✅ Correct Promote** | V5.26 new trade elevated & won | **`4`** | 🟢 **Alpha Generated (Fresh Base & Survived Boost)** |
| **❌ Bad Promote** | V5.26 new trade elevated & lost | **`0`** | 🔴 **False Positive Promotion** |
| **⚪ Concurring Win** | Both Legacy & Shadow traded and won | **`1`** | ⚪ Core Baseline Profit |
| **⚪ Concurring Loss** | Both Legacy & Shadow traded and lost | **`0`** | ⚪ Standard Market Loss |

**Performance Delta**: Legacy Total Realized: `-1.60R` vs **Shadow Hypothetical Total: `+9.20R` (Net $\Delta = +10.80R$)**

---

## 2. Daily Scanner Disagreement Breakdown

| Scanner | Legacy A Alerts | V5.26 Shadow Alerts | Net Diff (Δ) | Avoided Trades (Climax/Stale) | New Trades (Fresh/Survived) | Unchanged Trades |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **MultiTF 1H** | `1` | `5` | `+4` | 🔴 **`0`** | 🟢 **`4`** | ⚪ **`1`** |
| **MultiTF 5M** | `4` | `0` | `-4` | 🔴 **`4`** | 🟢 **`0`** | ⚪ **`0`** |
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

## 3. Signal Disagreement Log (The Manual Review Heart)

### A. Avoided Trades (Suppressed Stale Climax / Invalidated Breakdown)
These are candidates the Legacy system would have promoted, but V5.26 suppressed to prevent stale climax drag:

| Timestamp | Scanner | Symbol | Old Rank | New Rank | Catalyst State | Gem Age | Outcome R | Classification | Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `11:30:00` | **MultiTF 5M** | `RELIANCE` | `#2` | `#83` | **`INTRADAY_EXPIRED`** | `105m` | `+0.20R` | ❌ **`FALSE_AVOID`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| `11:30:00` | **MultiTF 5M** | `SBIN` | `#3` | `#88` | **`INTRADAY_EXPIRED`** | `105m` | `-1.00R` | ✅ **`CORRECT_AVOID`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| `11:30:00` | **MultiTF 5M** | `TATAMOTORS` | `#4` | `#90` | **`INTRADAY_EXPIRED`** | `105m` | `-1.00R` | ✅ **`CORRECT_AVOID`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| `11:30:00` | **MultiTF 5M** | `KALYANKJIL` | `#5` | `#94` | **`INTRADAY_EXPIRED`** | `105m` | `-1.00R` | ✅ **`CORRECT_AVOID`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |

### B. New Promoted Trades (Fresh EOD Bases & Survived Catalysts)
These are high-quality consolidation structures or surviving catalysts elevated by V5.26:

| Timestamp | Scanner | Symbol | Old Rank | New Rank | Catalyst State | Gem Age | Outcome R | Classification | Sizing | Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `10:15:00` | **MultiTF 1H** | `RELIANCE` | `#107` | `#1` | **`LIVE_GEM_ACTIVE`** | `50m` | `+2.20R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 50.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `TCS` | `#76` | `#2` | **`LIVE_GEM_ACTIVE`** | `55m` | `+1.80R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `INFY` | `#22` | `#3` | **`LIVE_GEM_ACTIVE`** | `55m` | `+1.80R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `LTIM` | `#77` | `#5` | **`LIVE_GEM_ACTIVE`** | `55m` | `+2.20R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |

---

## 4. False Veto Audit Tracker
Mandatory manual checkpoint: Monitor all `CATALYST_EXHAUSTED` and `CATALYST_INVALIDATED` signals after trade resolution to ensure no false negative structural rejection of genuine high-momentum leaders.

| Telemetry ID | Symbol | Scanner | Catalyst State | Tracked Outcome Actual R | MFE (R) | MAE (R) | Post-Trade Review Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `#44` | `TCS` | **Daily Builder** | `CATALYST_INVALIDATED` | `+0.20R` | `+0.50R` | `-1.00R` | **🔴 False Veto (Missed Runner)** |
| `#46` | `HDFCBANK` | **Daily Builder** | `CATALYST_EXHAUSTED` | `-0.40R` | `+0.10R` | `-1.00R` | **🟢 Correct Veto (Loss Avoided)** |
| `#49` | `BHARTIARTL` | **Daily Builder** | `CATALYST_INVALIDATED` | `-0.40R` | `+0.10R` | `-1.00R` | **🟢 Correct Veto (Loss Avoided)** |
| `#50` | `TATAMOTORS` | **Daily Builder** | `CATALYST_INVALIDATED` | `-0.60R` | `+0.10R` | `-1.00R` | **🟢 Correct Veto (Loss Avoided)** |
| `#58` | `TCS` | **Reversal** | `CATALYST_EXHAUSTED` | `+0.20R` | `+0.50R` | `-1.00R` | **🔴 False Veto (Missed Runner)** |
| `#60` | `HDFCBANK` | **Reversal** | `CATALYST_INVALIDATED` | `-0.85R` | `+0.10R` | `-1.05R` | **🟢 Correct Veto (Loss Avoided)** |
| `#62` | `SBIN` | **Reversal** | `CATALYST_EXHAUSTED` | `+0.20R` | `+0.50R` | `-1.00R` | **🔴 False Veto (Missed Runner)** |
| `#66` | `DIXON` | **Reversal** | `CATALYST_EXHAUSTED` | `-0.85R` | `+0.10R` | `-1.05R` | **🟢 Correct Veto (Loss Avoided)** |
| `#69` | `TRENT` | **Reversal** | `CATALYST_INVALIDATED` | `-1.00R` | `+0.10R` | `-1.20R` | **🟢 Correct Veto (Loss Avoided)** |
| `#71` | `RELIANCE` | **Pullback V2** | `CATALYST_EXHAUSTED` | `-0.60R` | `+0.10R` | `-1.00R` | **🟢 Correct Veto (Loss Avoided)** |
| `#72` | `TCS` | **Pullback V2** | `CATALYST_INVALIDATED` | `-0.60R` | `+0.10R` | `-1.00R` | **🟢 Correct Veto (Loss Avoided)** |
| `#73` | `INFY` | **Pullback V2** | `CATALYST_EXHAUSTED` | `+0.20R` | `+0.50R` | `-1.00R` | **🔴 False Veto (Missed Runner)** |
| `#76` | `SBIN` | **Pullback V2** | `CATALYST_EXHAUSTED` | `-0.40R` | `+0.10R` | `-1.00R` | **🟢 Correct Veto (Loss Avoided)** |
| `#77` | `BHARTIARTL` | **Pullback V2** | `CATALYST_EXHAUSTED` | `-1.00R` | `+0.10R` | `-1.20R` | **🟢 Correct Veto (Loss Avoided)** |
| `#78` | `TATAMOTORS` | **Pullback V2** | `CATALYST_EXHAUSTED` | `+0.20R` | `+0.50R` | `-1.00R` | **🔴 False Veto (Missed Runner)** |

---

## 5. Production Operational Status
- Current Active Production: **`V5.25_PRODUCTION`** (Unmodified)
- Parallel Shadow Observer: **`V5.26_SHADOW`** (Active in Background)
- Automatic Promotion: ❌ **DISABLED** (Manual Live Confirmation Required)