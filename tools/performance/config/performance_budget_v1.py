"""
Performance Budget Version 1 (V1)

These hard thresholds act as the foundational performance contracts for the Elite Breakout System.
Any architectural change that breaches these thresholds will fail the pipeline.
"""

BUDGET_VERSION = "v1"

# Latency Budgets (in seconds)
LATENCY_BUDGETS = {
    "startup": 5.0,
    "watchlist_generation": 30.0,
    "historical_fetch": 120.0,    # Max for 300 symbols
    "indicator_generation": 20.0,
    "scoring_evaluation": 25.0,
    "database_writes": 5.0,
    "cleanup": 10.0
}

# Memory Budgets (in Megabytes)
MEMORY_BUDGETS = {
    "peak_rss_mb": 600.0,
    "steady_state_mb": 450.0,
    "max_leak_per_rotation_mb": 10.0 # Strict limit on how much memory can climb over 30 days
}

# Garbage Collection Budgets
GC_BUDGETS = {
    "max_pause_ms": 100.0,
    "max_collections_per_run": 50 # If we hit GC >50 times, we are allocating too many temporary objects
}

# Operational Budgets
OPERATIONAL_BUDGETS = {
    "max_provider_fallbacks": 5,
    "max_dataframe_copies": 200 # Catch hidden deep copies
}
