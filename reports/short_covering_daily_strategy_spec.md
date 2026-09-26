# SHORT_COVERING_DAILY — STRATEGY SPECIFICATION

**Strategy Family:** `SHORT_COVERING_DAILY`  
**Governance Status:** `RESEARCH ONLY` (Not Approved for Live Production)  
**Timeframe:** Daily Session Close to T+1 Open  
**Core Mechanism:** Prior High Positioning (Crowding) -> Positioning Unwind (OI Contraction) -> Upward Price Response -> T+1 Open Execution  
**Execution Standard:** T+1 Open + 5 bps adverse slippage (Zero bar-close fill)  

### Signal Definition:
- Aggregate OI = Near-Month FUTSTK OI + Next-Month FUTSTK OI
- Price Return over N days > 0
- Aggregate OI Change over matching N days < 0
- Prior OI Build over 5 sessions > 0
