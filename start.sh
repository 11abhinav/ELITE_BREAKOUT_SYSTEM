#!/bin/bash

echo "🚀 Starting Elite Breakout System Supervisor..."

export PYTHONUNBUFFERED=1
# Reduce glibc native memory fragmentation
export MALLOC_ARENA_MAX=2
export PYTHONPATH="/app:/app/app:$PYTHONPATH"

# Run seed data synchronization in background so port 8000 binds in <1s for Coolify healthcheck
sync_seed_data() {
    if [ -d "/app/data_seed" ]; then
        mkdir -p /app/data
        # 1. Force overwrite canonical rebuilt dataset and metadata from certified image build
        if [ -f "/app/data_seed/canonical_pit_rebuilt.parquet" ]; then
            cp -f /app/data_seed/canonical_pit_rebuilt.parquet /app/data/
            cp -f /app/data_seed/canonical_pit_rebuilt_meta.json /app/data/ 2>/dev/null || true
            echo "✅ Certified canonical_pit_rebuilt.parquet synced from image to volume."
        fi
        # 2. Force overwrite pit_raw_filings from certified seed so git backfills take effect immediately
        if [ -d "/app/data_seed/pit_raw_filings" ]; then
            mkdir -p /app/data/pit_raw_filings
            cp -rf /app/data_seed/pit_raw_filings/* /app/data/pit_raw_filings/ 2>/dev/null || true
            echo "✅ Certified pit_raw_filings backfills synced from image to volume."
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
