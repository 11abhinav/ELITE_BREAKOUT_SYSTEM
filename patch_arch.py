import sys

with open("ARCHITECTURE.html", "r") as f:
    content = f.read()

new_section = """
## 43. V8 Data Quality Framework

### 43.1 System Invariant: Cache Protection
**Healthy cache can never be overwritten by lower-quality data.**
The V8 Data Quality Framework elevates data ingestion from a simple `df is not empty` check to a deterministic, lexicographical scoring pipeline. Incoming data is objectively evaluated and explicitly compared against the historical cache to protect the system from upstream provider anomalies (like Yahoo Finance BSE `.BO` truncations).

### 43.2 DataQualityReport & MarketData Wrapper
All data ingested from `DataFetcher` subclasses must be validated by the `DataQualityValidator`, which computes an objective `QualityScore` (0-100) based on:
1. **Schema & Type Validation:** Enforces exact column matching (`Open, High, Low, Close, Volume`) and numeric datatypes.
2. **Row Completeness (40 points):** Calculates expected rows dynamically based on the requested period/dates.
3. **Missing Values (20 points):** Penalizes `NaN` rates.
4. **Price Sanity (20 points):** Penalizes absurd prices (e.g., `High < Low`).
5. **Continuity & Freshness (20 points):** Evaluates timestamp monotonicity and time-aware market freshness.

The result is wrapped in an immutable `MarketData` dataclass that contains the dataframe, data source, and the generated `DataQualityReport`. Downstream scanners are abstracted away from these structures and unpack `MarketData` directly without knowing the data's origin.

### 43.3 Cache Decision Engine Merge Policy
The `price_cache.py` ingestion loop explicitly compares incoming data against the cache using a hierarchical formula:
- `RemoteScore = QualityScore × SOURCE_RELIABILITY[source]`
- `CacheScore = QualityScore × SOURCE_RELIABILITY["Cache"]`

If the remote data is inferior, the system executes a **KEEP_CACHE** policy, preserving the local `.parquet` data and marking it as `is_stale`. This guarantees years of accumulated historical data are strictly immunized against sudden API failures or regressions (tracked via the `MAX_HISTORY_SHRINK` threshold).
"""

# Find where to append it, right before <!-- Hidden Raw Markdown Data Store -->
if "<!-- Hidden Raw Markdown Data Store -->" in content:
    content = content.replace("<!-- Hidden Raw Markdown Data Store -->", new_section + "\n<!-- Hidden Raw Markdown Data Store -->")
    with open("ARCHITECTURE.html", "w") as f:
        f.write(content)
    print("Updated ARCHITECTURE.html with V8 Data Quality Framework")
else:
    print("Could not find insertion point in ARCHITECTURE.html")

