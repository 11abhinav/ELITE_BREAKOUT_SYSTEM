# ELITE BREAKOUT SYSTEM — AGENT OPERATING RULES

## CRITICAL BACKTESTING RULES
1. **MANDATORY REAL BSE / NSE MARKET DATA**:
   - All backtests, tournaments, parameter evaluations, and regime audits MUST use **actual BSE / NSE historical market price and volume data** (e.g. from `data/history/` 5m/1d parquet files, live exchange feeds, or broker APIs).
   - **Zero Dummy Data**: NEVER use synthetic, simulated, or parametric generated dummy data for backtests.
2. **STRICT POINT-IN-TIME CAUSALITY (ZERO LOOKAHEAD / ZERO FORWARD-LOOKING)**:
   - At the time of signal generation ($t$), only data available up to that timestamp ($T \le t$) can be used.
   - Future session data ($T > t$) is strictly prohibited during feature calculation, gating, or ranking.
3. **CALENDAR INVARIANTS**:
   - Trading days only (Monday to Friday, excluding official NSE/BSE holidays).
   - Saturday = 0, Sunday = 0.

## SYSTEM INVARIANTS
- Timezone: Asia/Kolkata (IST).
- Currency: Indian Rupee (INR / ₹ / Rs).
- Governance: All production parameter promotions require dual-track registration and audit trail.
