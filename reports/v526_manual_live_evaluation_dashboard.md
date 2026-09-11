# V5.26 Live Shadow Telemetry & Manual Evaluation Dashboard

**Configuration Version**: `V5.26_SHADOW`  
**Generated At**: `2026-09-11T13:01:09.273832`  
**Source Telemetry Database**: `data/shadow_telemetry.db`  

---

## 1. Shadow Advantage Ledger (Net Live Delta vs Legacy Production)

| Scanner | Disagreements | Correct Avoid Savings (+R) | False Avoid Cost (-R) | Correct Promote Gains (+R) | Bad Promote Losses (-R) | **Net Shadow ΔR** |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **MultiTF 1H** | `9` | `+0.00R` | `-2.50R` | `+16.10R` | `-0.00R` | **`+13.60R`** |
| **MultiTF 5M** | `7` | `+5.25R` | `-0.20R` | `+0.00R` | `-0.00R` | **`+5.05R`** |
| **Short Covering** | `0` | `+0.00R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+0.00R`** |
| **Daily Builder** | `0` | `+0.00R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+0.00R`** |
| **Reversal** | `0` | `+0.00R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+0.00R`** |
| **Pullback V2** | `0` | `+0.00R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+0.00R`** |
| **Multibagger** | `0` | `+0.00R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+0.00R`** |
| **EOD Breakout** | `0` | `+0.00R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+0.00R`** |
| **Accumulation VCP** | `0` | `+0.00R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+0.00R`** |
| **Wealth Engine** | `0` | `+0.00R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+0.00R`** |
| **Technical Ahat** | `0` | `+0.00R` | `-0.00R` | `+0.00R` | `-0.00R` | **`+0.00R`** |

**Total Disagreements Audited**: `16` | **Aggregate Net Advantage**: **`+18.65R`**

---

## 2. Executive 4-Way Outcome Scorecard

| Four-Way Classification | Outcome Meaning | Trade Count | R Impact / Verdict |
| :--- | :--- | :--- | :--- |
| **✅ Correct Avoid** | Legacy would trade & lose; V5.26 suppressed | **`157`** | 🟢 **Capital Preserved (Eliminated Stale Climax Drag)** |
| **❌ False Avoid** | Legacy would trade & win; V5.26 suppressed | **`141`** | 🔴 **Opportunity Cost (Structural Filter False Veto)** |
| **✅ Correct Promote** | V5.26 new trade elevated & won | **`8`** | 🟢 **Alpha Generated (Fresh Base & Survived Boost)** |
| **❌ Bad Promote** | V5.26 new trade elevated & lost | **`0`** | 🔴 **False Positive Promotion** |
| **⚪ Concurring Win** | Both Legacy & Shadow traded and won | **`2`** | ⚪ Core Baseline Profit |
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
| **Multiple Structural Failures** | Structural Filter Rule Triggered | **`138`** | `95.2%` |
| **Intraday Gem Expired (>60m TTL)** | Structural Filter Rule Triggered | **`7`** | `4.8%` |

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
| `#62` | `SBIN` | **Reversal** | `CATALYST_EXHAUSTED` | `+0.20R` | `+0.50R` | `-1.00R` | **🔴 False Veto (Missed Runner)** |
| `#66` | `DIXON` | **Reversal** | `CATALYST_EXHAUSTED` | `-0.85R` | `+0.10R` | `-1.05R` | **🟢 Correct Veto (Loss Avoided)** |
| `#69` | `TRENT` | **Reversal** | `CATALYST_INVALIDATED` | `-1.00R` | `+0.10R` | `-1.20R` | **🟢 Correct Veto (Loss Avoided)** |
| `#71` | `RELIANCE` | **Pullback V2** | `CATALYST_EXHAUSTED` | `-0.60R` | `+0.10R` | `-1.00R` | **🟢 Correct Veto (Loss Avoided)** |
| `#72` | `TCS` | **Pullback V2** | `CATALYST_INVALIDATED` | `-0.60R` | `+0.10R` | `-1.00R` | **🟢 Correct Veto (Loss Avoided)** |
| `#73` | `INFY` | **Pullback V2** | `CATALYST_EXHAUSTED` | `+0.20R` | `+0.50R` | `-1.00R` | **🔴 False Veto (Missed Runner)** |
| `#76` | `SBIN` | **Pullback V2** | `CATALYST_EXHAUSTED` | `-0.40R` | `+0.10R` | `-1.00R` | **🟢 Correct Veto (Loss Avoided)** |
| `#77` | `BHARTIARTL` | **Pullback V2** | `CATALYST_EXHAUSTED` | `-1.00R` | `+0.10R` | `-1.20R` | **🟢 Correct Veto (Loss Avoided)** |
| `#78` | `TATAMOTORS` | **Pullback V2** | `CATALYST_EXHAUSTED` | `+0.20R` | `+0.50R` | `-1.00R` | **🔴 False Veto (Missed Runner)** |
| `#83` | `TRENT` | **Pullback V2** | `CATALYST_INVALIDATED` | `-1.00R` | `+0.10R` | `-1.20R` | **🟢 Correct Veto (Loss Avoided)** |
| `#85` | `RELIANCE` | **Multibagger** | `CATALYST_EXHAUSTED` | `+0.20R` | `+0.50R` | `-1.00R` | **🔴 False Veto (Missed Runner)** |
| `#86` | `TCS` | **Multibagger** | `CATALYST_INVALIDATED` | `-0.40R` | `+0.10R` | `-1.00R` | **🟢 Correct Veto (Loss Avoided)** |
| `#87` | `INFY` | **Multibagger** | `CATALYST_INVALIDATED` | `-0.60R` | `+0.10R` | `-1.00R` | **🟢 Correct Veto (Loss Avoided)** |
| `#88` | `HDFCBANK` | **Multibagger** | `CATALYST_INVALIDATED` | `-0.85R` | `+0.10R` | `-1.05R` | **🟢 Correct Veto (Loss Avoided)** |

---

## 6. Live Validation Gate #1 Progress & Operational Status
- **Sample Size Target**: `N >= 100` Live Resolved Disagreements (Current: `16`)
- **Current Net Shadow Advantage**: **`+18.65R`**
- **False Veto Rate**: `45.8%`
- **Current Active Production**: **`V5.25_PRODUCTION`** (Unmodified)
- **Parallel Shadow Observer**: **`V5.26_SHADOW`** (Active in Background)
- **Automatic Promotion**: ❌ **DISABLED** (Manual Live Confirmation Required at Gate #1 Review)