# REPORT 1: Current Production Architecture Audit

**Scanner**: Short Covering Early Ignition Engine  
**Core Codebase**: `app/short_covering/short_covering_scanner.py`, `app/short_covering/short_position_detector.py`  
**Execution Cadence**: 
- Layer 1 (EOD): Daily 19:15 IST on F&O Universe
- Layer 2 (Intraday): 5-Minute Continuous Scanning (09:20 – 15:25 IST)

## 1. Production Architecture Specifications
- **Universe**: Liquid F&O Stocks (Point-in-Time Active Universe)
- **EOD Quality Score Gate**: >= 50.0 (OI Expansion + SBR + Falling Price + RSI)
- **EOD Watchlist Restriction**: Top 35 Symbols
- **5m Ignition Score Gate**: >= 65.0
- **5m OI Contraction Gate**: <= -0.50%
- **Volume Surge Gate**: >= 1.25x 10-bar average
- **State Progression**: High conviction -> Immediate alert; Moderate conviction -> IGNITION_CANDIDATE confirmation.
