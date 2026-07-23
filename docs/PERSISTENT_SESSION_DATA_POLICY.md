# Persistent Session Data Policy (Critical)

One of the biggest performance regressions comes from repeatedly fetching large datasets that rarely change during the trading session.

**Do not optimize these datasets out of memory simply to reduce RAM usage.**

If a large dataset is expensive to fetch, parse, or compute, and it will be reused by one or more recurring scanner runs during the same trading session, it should remain in a shared in-memory cache.

## Guiding Principle

**Network calls are the most expensive operation.**
**Disk I/O is slower than RAM.**
**Repeated computation is slower than reusing memory.**

Therefore:
> **Fetch Once → Compute Once → Cache Once → Reuse Many Times**

instead of

> **Fetch → Free → Fetch Again → Recompute → Repeat**

The latter wastes CPU, network bandwidth, API quota, and significantly increases scanner execution time.

## Smart Cache Eviction
Do **not** evict data solely because it is large. Evict data only when:
* It is no longer needed by any scanner.
* It has become stale and requires replacement.
* A newer version supersedes the cached data.

Memory should be managed intelligently based on reuse patterns, not simply by minimizing RAM consumption.

## Performance First
The objective is **minimum end-to-end scanner execution time**, not minimum memory usage.
It is acceptable to dedicate additional RAM to hold reusable datasets if that avoids repeated network requests, repeated preprocessing, and repeated calculations.
**RAM should be treated as a strategic performance cache, not as a resource to minimize at all costs.**
