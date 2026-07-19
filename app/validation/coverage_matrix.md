# Validation Coverage Audit Matrix

This matrix tracks the validation status of every external data source ingested by the system.
No new data source may be added to production unless its status is `✅` (Complete).

| Dataset | Validator | Status | Blocking? | Cache? | Notes |
|---|---|---|---|---|---|
| Price | `PriceValidator` | ✅ | YES | YES | Foundation for EOD scanners & multi-timeframe evaluation. |
| Bhavcopy | `BhavcopyValidator` | ❌ Planned | YES | YES | Required for volume/delivery confirmation. |
| Delivery | `DeliveryValidator` | ❌ Planned | YES | YES | Crucial for institutional footprint tracking. |
| Symbol Master | `SymbolMasterValidator`| ❌ Planned | YES | YES | Determines available trading universe. |
| Fundamentals | `FundamentalsValidator`| ❌ | NO | YES | Used for CANSLIM and fundamental filtering. |
| Corporate Actions| `CorpActionValidator` | ❌ | NO | YES | Adjusts historical prices for splits/bonuses. |
| Index Constituents| `IndexValidator` | ❌ | NO | YES | Tracks Nifty50/500 constituents. |
| Live Quotes | `LiveQuoteValidator` | ❌ | YES | NO | Supports real-time execution and intraday checks. |
| Market Breadth | `BreadthValidator` | ❌ | NO | NO | Used for regime filters (ADX, advancing/declining). |
| Holidays | `HolidayValidator` | ❌ | NO | NO | Impacts expected cache row estimations. |

## Validation Tiers
Validators must be implemented in the following dependency order:

- **Tier 1 (Core Trading):** Price, Bhavcopy, Delivery, Symbol Master
- **Tier 2 (Decision Support):** Fundamentals, Corporate Actions, Index Constituents
- **Tier 3 (Runtime):** Live Quotes, Market Breadth
- **Tier 4 (Reference Data):** Holidays
