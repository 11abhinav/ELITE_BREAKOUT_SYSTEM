#!/bin/bash

echo "🚀 Starting Elite Breakout System Supervisor..."

export PYTHONUNBUFFERED=1
# Increase OS file descriptor limit to avoid [Errno 24] Too many open files
ulimit -n 65535 2>/dev/null || ulimit -n 4096 2>/dev/null || true
# Reduce glibc native memory fragmentation
export MALLOC_ARENA_MAX=2
export PYTHONPATH="/app:/app/app:$PYTHONPATH"

# Run seed data synchronization in background so port 8000 binds in <1s for Coolify healthcheck
sync_seed_data() {
    if [ -d "/app/data_seed" ]; then
        mkdir -p /app/data
        # 1. Seed canonical rebuilt dataset ONLY if not already present on persistent volume
        if [ ! -f "/app/data/canonical_pit_rebuilt.parquet" ] && [ -f "/app/data_seed/canonical_pit_rebuilt.parquet" ]; then
            cp -f /app/data_seed/canonical_pit_rebuilt.parquet /app/data/
            cp -f /app/data_seed/canonical_pit_rebuilt_meta.json /app/data/ 2>/dev/null || true
            echo "✅ Certified canonical_pit_rebuilt.parquet seeded from image to volume."
        fi
        # 2. Seed pit_raw_filings without clobbering runtime-fetched filings (-n = no-clobber)
        if [ -d "/app/data_seed/pit_raw_filings" ]; then
            mkdir -p /app/data/pit_raw_filings
            cp -rn /app/data_seed/pit_raw_filings/* /app/data/pit_raw_filings/ 2>/dev/null || true
            echo "✅ Certified pit_raw_filings seeded to volume without clobbering runtime filings."
        fi
        # 3. Synchronize other seed files without overwriting dynamic runtime state
        cp -rn /app/data_seed/* /app/data/ 2>/dev/null || cp -r /app/data_seed/* /app/data/ 2>/dev/null || true
        echo "✅ Data seed sync complete."
    fi
}
# Synchronously sync critical certified seed data before main.py boots
sync_seed_data

while true; do
    echo "▶️ Launching app/main.py..."
    python3 -u app/main.py
    EXIT_CODE=$?
    
    echo "⚠️ main.py crashed or exited with code $EXIT_CODE."
    
    if [ $EXIT_CODE -eq 0 ]; then
        echo "🛑 Clean exit detected. Stopping supervisor loop."
        break
    fi
    
    echo "⏳ Respawning in 5 seconds..."
    sleep 5
done
