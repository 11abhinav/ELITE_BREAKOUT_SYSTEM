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
        # 1. Update canonical rebuilt dataset if missing or if image seed is newer
        if [ -f "/app/data_seed/canonical_pit_rebuilt.parquet" ]; then
            cp -u -f /app/data_seed/canonical_pit_rebuilt.parquet /app/data/ 2>/dev/null || cp -f /app/data_seed/canonical_pit_rebuilt.parquet /app/data/ 2>/dev/null || true
            cp -u -f /app/data_seed/canonical_pit_rebuilt_meta.json /app/data/ 2>/dev/null || cp -f /app/data_seed/canonical_pit_rebuilt_meta.json /app/data/ 2>/dev/null || true
            echo "✅ Certified canonical_pit_rebuilt.parquet synced from image seed to volume."
        fi
        # 2. Update pit_raw_filings with newer filings from image seed (-u = update if newer)
        if [ -d "/app/data_seed/pit_raw_filings" ]; then
            mkdir -p /app/data/pit_raw_filings
            cp -u -f /app/data_seed/pit_raw_filings/* /app/data/pit_raw_filings/ 2>/dev/null || cp -rn /app/data_seed/pit_raw_filings/* /app/data/pit_raw_filings/ 2>/dev/null || true
            echo "✅ Certified pit_raw_filings synced to volume with update-newer flag."
        fi
        # 3. Synchronize manifest and other seed files
        if [ -f "/app/data_seed/pit_raw_filings_manifest.json" ]; then
            cp -u -f /app/data_seed/pit_raw_filings_manifest.json /app/data/ 2>/dev/null || cp -f /app/data_seed/pit_raw_filings_manifest.json /app/data/ 2>/dev/null || true
        fi
        cp -u -rn /app/data_seed/* /app/data/ 2>/dev/null || true
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
