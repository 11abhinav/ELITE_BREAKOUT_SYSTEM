# Changelog

All notable changes to the Elite Breakout System architecture and capabilities will be documented in this file.

## [v6.3.0] - Architecture Governance Release
### Added
*   **Dataset Provenance**: Injected `df.attrs` at runtime to establish a permanent audit trail of data acquisition sources.
*   **Dataset Registry**: Centralized dataset ownership to enforce deterministic memory and lifecycle management.
*   **Unified Fetcher & Provider Selector**: Established a single I/O boundary to enforce provider selection policy and safe fallback chains.
*   **Indicator Manager**: Centralized technical indicator computation (SMA, EMA, RSI, ATR) to eliminate redundant execution across multiple scanners.
*   **ScraperAPI Integration**: Formalized the residential proxy layer specifically for NSE data routes (Bhavcopy, Delivery, Promoter Pledge) to prevent automated IP bans.
*   **Granular Locking**: Implemented resource-scoped network locks to preserve parallel CPU execution for scanners.

### Removed
*   All unmanaged mutable globals containing business logic data.
*   All direct, untracked `fyers.quotes()` and `yf.download()` calls embedded inside scanner or application logic.
