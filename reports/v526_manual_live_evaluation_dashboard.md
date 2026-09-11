# V5.26 Live Shadow Telemetry & Manual Evaluation Dashboard

**Configuration Version**: `V5.26_SHADOW`  
**Generated At**: `2026-09-11T15:50:02.348154`  
**Source Telemetry Database**: `data/shadow_telemetry.db`  

---

## 1. Shadow Advantage Ledger (Net Live Delta vs Legacy Production)

| Scanner | Disagreements | Correct Avoid Savings (+R) | False Avoid Cost (-R) | Correct Promote Gains (+R) | Bad Promote Losses (-R) | **Net Shadow ΔR** |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **MultiTF 1H** | `18` | `+0.00R` | `-6.20R` | `+25.90R` | `-1.50R` | **`+18.20R`** |
| **MultiTF 5M** | `11` | `+7.65R` | `-0.40R` | `+0.00R` | `-0.00R` | **`+7.25R`** |
| **Short Covering** | `3` | `+2.70R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+2.70R`** |
| **Daily Builder** | `2` | `+0.00R` | `-0.00R` | `+1.20R` | `-0.75R` | **`+0.45R`** |
| **Reversal** | `0` | `+0.00R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+0.00R`** |
| **Pullback V2** | `0` | `+0.00R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+0.00R`** |
| **Multibagger** | `0` | `+0.00R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+0.00R`** |
| **EOD Breakout** | `0` | `+0.00R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+0.00R`** |
| **Accumulation VCP** | `0` | `+0.00R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+0.00R`** |
| **Wealth Engine** | `0` | `+0.00R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+0.00R`** |
| **Technical Ahat** | `0` | `+0.00R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+0.00R`** |

**Total Disagreements Audited**: `34` | **Aggregate Net Advantage**: **`+28.60R`**

---

## 2. Executive 4-Way Outcome Scorecard

| Four-Way Classification | Outcome Meaning | Trade Count | R Impact / Verdict |
| :--- | :--- | :--- | :--- |
| **✅ Correct Avoid** | Legacy would trade & lose; V5.26 suppressed | **`317`** | 🟢 **Capital Preserved (Eliminated Stale Climax Drag)** |
| **❌ False Avoid** | Legacy would trade & win; V5.26 suppressed | **`279`** | 🔴 **Opportunity Cost (Structural Filter False Veto)** |
| **✅ Correct Promote** | V5.26 new trade elevated & won | **`14`** | 🟢 **Alpha Generated (Fresh Base & Survived Boost)** |
| **❌ Bad Promote** | V5.26 new trade elevated & lost | **`3`** | 🔴 **False Positive Promotion** |
| **⚪ Concurring Win** | Both Legacy & Shadow traded and won | **`3`** | ⚪ Core Baseline Profit |
| **⚪ Concurring Loss** | Both Legacy & Shadow traded and lost | **`0`** | ⚪ Standard Market Loss |

---

## 3. Structural Failure Attribution (Why Candidates Were Avoided)

| Structural Failure Dimension | Root Cause Mechanism | Suppressed Count | Failure Rate (%) |
| :--- | :--- | :--- | :--- |
| **Extension Breach (>3.20R)** | Structural Filter Rule Triggered | **`0`** | `0.0%` |
| **CLV Failure (<0.68)** | Structural Filter Rule Triggered | **`0`** | `0.0%` |
| **Volume Decay (<1.10x)** | Structural Filter Rule Triggered | **`0`** | `0.0%` |
| **VWAP Breakdown (<VWAP)** | Structural Filter Rule Triggered | **`0`** | `0.0%` |
| **ORB Breakdown** | Structural Filter Rule Triggered | **`0`** | `0.0%` |
| **Runway Shortfall (<2.50 ATR)** | Structural Filter Rule Triggered | **`0`** | `0.0%` |
| **Multiple Structural Failures** | Structural Filter Rule Triggered | **`263`** | `96.0%` |
| **Intraday Gem Expired (>60m TTL)** | Structural Filter Rule Triggered | **`11`** | `4.0%` |

---

## 4. Signal Disagreement Log (The Manual Review Heart)

### A. Avoided Trades (Suppressed Stale Climax / Invalidated Breakdown)

| Timestamp | Scanner | Symbol | Old Rank | New Rank | Catalyst State | Gem Age | Outcome R | Classification | Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `11:30:00` | **MultiTF 5M** | `RELIANCE` | `#2` | `#83` | **`INTRADAY_EXPIRED`** | `105m` | `+0.20R` | ❌ **`FALSE_AVOID`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| `11:30:00` | **MultiTF 5M** | `SBIN` | `#3` | `#88` | **`INTRADAY_EXPIRED`** | `105m` | `-1.00R` | ✅ **`CORRECT_AVOID`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| `11:30:00` | **MultiTF 5M** | `TATAMOTORS` | `#4` | `#90` | **`INTRADAY_EXPIRED`** | `105m` | `-1.00R` | ✅ **`CORRECT_AVOID`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| `11:30:00` | **MultiTF 5M** | `KALYANKJIL` | `#5` | `#94` | **`INTRADAY_EXPIRED`** | `105m` | `-1.00R` | ✅ **`CORRECT_AVOID`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `ZOMATO` | `#2` | `#13` | **`LIVE_GEM_ACTIVE`** | `30m` | `+2.50R` | ❌ **`FALSE_AVOID`** | Live Intraday Gem Active (Age 30.0m <= 60m TTL) |
| `11:30:00` | **MultiTF 5M** | `LTIM` | `#3` | `#88` | **`INTRADAY_EXPIRED`** | `105m` | `-1.00R` | ✅ **`CORRECT_AVOID`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| `11:30:00` | **MultiTF 5M** | `DIXON` | `#4` | `#89` | **`INTRADAY_EXPIRED`** | `105m` | `-0.40R` | ✅ **`CORRECT_AVOID`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| `11:30:00` | **MultiTF 5M** | `ZOMATO` | `#5` | `#93` | **`INTRADAY_EXPIRED`** | `105m` | `-0.85R` | ✅ **`CORRECT_AVOID`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `KALYANKJIL` | `#1` | `#9` | **`LIVE_GEM_ACTIVE`** | `30m` | `+1.20R` | ❌ **`FALSE_AVOID`** | Live Intraday Gem Active (Age 30.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `ZOMATO` | `#2` | `#11` | **`LIVE_GEM_ACTIVE`** | `30m` | `+2.50R` | ❌ **`FALSE_AVOID`** | Live Intraday Gem Active (Age 30.0m <= 60m TTL) |
| `11:30:00` | **MultiTF 5M** | `ICICIBANK` | `#3` | `#95` | **`INTRADAY_EXPIRED`** | `105m` | `+0.20R` | ❌ **`FALSE_AVOID`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| `11:30:00` | **MultiTF 5M** | `DIXON` | `#4` | `#97` | **`INTRADAY_EXPIRED`** | `105m` | `-1.00R` | ✅ **`CORRECT_AVOID`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| `13:00:00` | **Short Covering** | `TATAMOTORS` | `#5` | `#76` | **`SHORT_COVERING_BASELINE`** | `195m` | `-0.90R` | ✅ **`CORRECT_AVOID`** | Standard Short Covering: Size 0.50R |
| `11:30:00` | **MultiTF 5M** | `TRENT` | `#1` | `#95` | **`INTRADAY_EXPIRED`** | `105m` | `-0.40R` | ✅ **`CORRECT_AVOID`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |
| `11:30:00` | **MultiTF 5M** | `ZOMATO` | `#2` | `#96` | **`INTRADAY_EXPIRED`** | `105m` | `-1.00R` | ✅ **`CORRECT_AVOID`** | Intraday Gem Expired (Age 105.0m > 60m TTL) |

### B. New Promoted Trades (Fresh EOD Bases & Survived Catalysts)

| Timestamp | Scanner | Symbol | Old Rank | New Rank | Catalyst State | Gem Age | Outcome R | Classification | Sizing | Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `10:15:00` | **MultiTF 1H** | `RELIANCE` | `#107` | `#1` | **`LIVE_GEM_ACTIVE`** | `50m` | `+2.20R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 50.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `TCS` | `#76` | `#2` | **`LIVE_GEM_ACTIVE`** | `55m` | `+1.80R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `INFY` | `#22` | `#3` | **`LIVE_GEM_ACTIVE`** | `55m` | `+1.80R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `LTIM` | `#77` | `#5` | **`LIVE_GEM_ACTIVE`** | `55m` | `+2.20R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `RELIANCE` | `#28` | `#1` | **`LIVE_GEM_ACTIVE`** | `55m` | `+2.20R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `TCS` | `#75` | `#2` | **`LIVE_GEM_ACTIVE`** | `55m` | `+1.20R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `INFY` | `#104` | `#3` | **`LIVE_GEM_ACTIVE`** | `50m` | `+2.50R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 50.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `SBIN` | `#29` | `#5` | **`LIVE_GEM_ACTIVE`** | `55m` | `+2.20R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `RELIANCE` | `#68` | `#1` | **`LIVE_GEM_ACTIVE`** | `55m` | `-0.75R` | ❌ **`BAD_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `INFY` | `#31` | `#2` | **`LIVE_GEM_ACTIVE`** | `55m` | `+1.80R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `HDFCBANK` | `#69` | `#3` | **`LIVE_GEM_ACTIVE`** | `55m` | `+2.50R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `SBIN` | `#70` | `#4` | **`LIVE_GEM_ACTIVE`** | `55m` | `+2.50R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `BHARTIARTL` | `#71` | `#5` | **`LIVE_GEM_ACTIVE`** | `55m` | `-0.75R` | ❌ **`BAD_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `RELIANCE` | `#71` | `#4` | **`LIVE_GEM_ACTIVE`** | `55m` | `+1.80R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 55.0m <= 60m TTL) |
| `10:15:00` | **MultiTF 1H** | `TCS` | `#53` | `#5` | **`LIVE_GEM_ACTIVE`** | `15m` | `+1.20R` | ✅ **`CORRECT_PROMOTE`** | `1.00R` | Live Intraday Gem Active (Age 15.0m <= 60m TTL) |

---

## 5. False Veto Audit Tracker

| Telemetry ID | Symbol | Scanner | Catalyst State | Tracked Outcome Actual R | MFE (R) | MAE (R) | Post-Trade Review Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `#44` | `TCS` | **Daily Builder** | `CATALYST_INVALIDATED` | `+0.20R` | `+0.50R` | `-1.00R` | **🔴 False Veto (Missed Runner)** |
| `#46` | `HDFCBANK` | **Daily Builder** | `CATALYST_EXHAUSTED` | `-0.40R` | `+0.10R` | `-1.00R` | **🟢 Correct Veto (Loss Avoided)** |
| `#49` | `BHARTIARTL` | **Daily Builder** | `CATALYST_INVALIDATED` | `-0.40R` | `+0.10R` | `-1.00R` | **🟢 Correct Veto (Loss Avoided)** |
| `#50` | `TATAMOTORS` | **Daily Builder** | `CATALYST_INVALIDATED` | `-0.60R` | `+0.10R` | `-1.00R` | **🟢 Correct Veto (Loss Avoided)** |
| `#58` | `TCS` | **Reversal** | `CATALYST_EXHAUSTED` | `+0.20R` | `+0.50R` | `-1.00R` | **🔴 False Veto (Missed Runner)** |
| `#60` | `HDFCBANK` | **Reversal** | `CATALYST_INVALIDATED` | `-0.85R` | `+0.10R` | `-1.05R` | **🟢 Correct Veto (Loss Avoided)** |

---

## 6. Live Validation Gate #1 Progress & Operational Status
- **Sample Size Target**: `N >= 100` Live Resolved Disagreements (Current: `34`)
- **Current Net Shadow Advantage**: **`+28.60R`**
- **False Veto Rate**: `45.3%`
- **Current Active Production**: **`V5.25_PRODUCTION`** (Unmodified)
- **Parallel Shadow Observer**: **`V5.26_SHADOW`** (Active in Background)
- **Automatic Promotion**: ❌ **DISABLED** (Manual Live Confirmation Required at Gate #1 Review)