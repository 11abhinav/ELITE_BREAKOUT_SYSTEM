# Validation Taxonomy

The validation framework implements two foundational architectural patterns when validating datasets. A validator always operates on the natural granularity of its dataset.

Every validator inheriting from `BaseValidator` must explicitly document which category it belongs to.

| Validator | Category | Validation Unit |
| :--- | :--- | :--- |
| **Price** | Time-Series | Symbol × Time |
| **Delivery** | Time-Series | Symbol × Time |
| **Corporate Actions** | Time-Series | Symbol × Time |
| **Fundamentals** | Time-Series (if historical) | One entity over time |
| **Bhavcopy** | Cross-Section | Market × Day |
| **Symbol Master** | Cross-Section | Market Snapshot |
| **Constituents** | Cross-Section | Index Snapshot |

## Category 1: Time-Series Validators

**Input:** One entity across time.
**Responsibility:** Ensures temporal consistency, completeness, and continuity for a single asset.

**Typical Checks:**
- Missing trading dates (Gaps)
- Timestamp monotonic ordering
- Historical shrink (unexpected drops in row counts)
- Duplicate timestamps
- Time-series continuity
- Rolling anomalies

## Category 2: Cross-Section Validators

**Input:** Many entities for one point in time.
**Responsibility:** Ensures structural integrity, dataset completeness, and exchange-wide consistency for a snapshot.

**Typical Checks:**
- Duplicate symbols
- Missing symbols
- Dataset completeness against expectations
- Exchange-wide schema consistency
- Cross-sectional uniqueness (e.g., Duplicate ISINs)
