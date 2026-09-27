# WEALTH_EXIT_V1 SPECIFICATION & RESEARCH CHARTER
**Status:** RESEARCH ONLY (Locked Backtest Architecture)  
**Objective:** Compare long-term wealth exit against existing production Arm B exit and pure hold.

---

### ARMS UNDER COMPARISON
1. **Arm A (Existing Arm B Control):**
   - 50% at +1.5R, 50% at +2.5R
   - Breakeven defense at +1.0R
   - 0.5 ATR high-water trailing stop
   - 15-day maximum holding cap
2. **Arm B (Wealth Exit V1):**
   - No profit target
   - No 15-day cap
   - Normal exit only after confirmed weakness at daily close $T$ -> Executed at $T+1$ Open
   - **Structural Weakness:** 2 consecutive closes below SMA50 OR close below 20-day lowest close
   - **Secondary Confirmation (at least 1 on same date):**
     - SMA50(T) <= SMA50(T-5)
     - 10-day relative return vs composite benchmark <= -5%
     - At least 2 distribution days in prior 10 sessions (Close < Open and Vol >= 1.5x Avg20)
3. **Arm C (Pure-Hold Diagnostic):**
   - Buy at T+1 Open
   - No normal exit; held until certified terminal date (2026-09-25 Close)

---
*Authored by Elite Breakout System Research Engine.*
