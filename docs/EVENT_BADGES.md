# Corporate Action Event Framework Documentation (v1.0)

The **Corporate Action Event Framework** provides a decoupled, stateless, and extensible architecture for decorating stock objects with priority-ranked event badges (e.g. Earnings `E`, Dividends `D`, Stock Splits `S`, Bonuses `B`) across all backend API endpoints and frontend dashboards.

---

## 1. Architectural Layers & Responsibilities

```
    CorporateEventRepository           TradingCalendar
  (DB Query / Data Access Layer)     (Cross-Cutting Trading Days)
               │                                │
               └───────────────┬────────────────┘
                               │
                               ▼
                      CorporateEventCache
              (Cache Lifecycle, TTL & Fallbacks)
                               │
                               ▼
                    CorporateEventPipeline
            (Pluggable Contributor Registry)
                               │
                               ▼
               decorate_events(stocks) -> Pure Function
                               │
                               ▼
                 Versioned Semantic JSON Payload
                               │
                               ▼
         shared_ui.js -> renderEventBadges(event_badges)
       (Client-Side CSS Styling, Tooltips & +N Overflow)
```

1. **`app/trading_calendar.py` (`TradingCalendar`)**:
   - Cross-cutting service computing actual trading sessions between dates (`days_between()`), skipping weekends and official NSE market holidays.

2. **`app/corporate_events.py`**:
   - **`CorporateEventRepository`**: Isolated database data access layer (`fetch_all_events()`).
   - **`CorporateEventCache`**: Manages cache lifecycle, 1-hour TTL, and fallback to stale snapshots on failure.
   - **`EventContributor`**: Abstract provider class (`EarningsContributor`, etc.).
   - **`CorporateEventPipeline`**: Registry aggregating event contributors.
   - **`decorate_events()`**: Stateless, pure functional transformer returning immutable copies decorated with `event_badges`.

---

## 2. Payload Schema Contract (`schema_version: 1`)

Every decorated stock object contains a standardized `event_badges` list:

```json
{
  "symbol": "TATAMOTORS",
  "company_name": "Tata Motors Limited",
  "schema_version": 1,
  "event_badges": [
    {
      "type": "earnings",
      "label": "E in 3d",
      "priority": 100,
      "status": "UPCOMING",
      "metadata": {
        "date": "2026-08-06",
        "days": 3,
        "date_status": "CONFIRMED"
      }
    }
  ]
}
```

### Event Priority Hierarchy (`EventPriority`)

| Event Type | Priority Value | Label | Status Classification |
|---|---|---|---|
| **`EARNINGS`** | **100** | `E in 3d` / `E 2d ago` | `UPCOMING` (`0 <= days <= 7`) / `RECENT` (`-7 <= days < 0`) |
| **`DIVIDEND`** | **80** | `D` | `UPCOMING` / `RECENT` |
| **`SPLIT`** | **70** | `S` | `UPCOMING` / `RECENT` |
| **`BONUS`** | **60** | `B` | `UPCOMING` / `RECENT` |

---

## 3. How to Add a New Event Type (e.g. Dividends)

To add a new corporate action event type (e.g. Dividends `D`):

1. Inherit from `EventContributor` in `app/corporate_events.py`:
   ```python
   class DividendContributor(EventContributor):
       def contribute(self, symbol: str, symbol_events: dict, calendar: TradingCalendar, current_date: date) -> list:
           # Evaluate dividend date & return badge object if within window
           return [{
               "type": "dividend",
               "label": "D",
               "priority": int(EventPriority.DIVIDEND),
               "status": "UPCOMING",
               "metadata": { "date": dividend_date, "amount": 15.0 }
           }]
   ```

2. Register the contributor in `CorporateEventPipeline`:
   ```python
   pipeline.register_contributor(DividendContributor())
   ```

3. Frontend JavaScript (`shared_ui.js`) automatically handles sorting by priority and rendering badges without any UI layout changes.

---

## 4. Client-Side Rendering (`renderEventBadges`)

The client-side renderer in `static/shared_ui.js`:
- Sorts badges by `priority` descending.
- Displays up to `maxDisplay` badges (default = 2).
- Displays a touch-friendly and accessible `+N` overflow pill for remaining events with hover/tap popover tooltips.
