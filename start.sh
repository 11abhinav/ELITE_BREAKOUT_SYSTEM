#!/bin/bash

echo "🚀 Starting Elite Breakout System Supervisor..."

# Reduce glibc native memory fragmentation
export MALLOC_ARENA_MAX=2
export PYTHONPATH="/app:/app/app:$PYTHONPATH"

# Ensure /app/data has all required seed data even if a persistent volume is mounted
if [ -d "/app/data_seed" ]; then
    mkdir -p /app/data
    # 1. Update pit_raw_filings and configuration files
    cp -ru /app/data_seed/* /app/data/ 2>/dev/null || cp -r /app/data_seed/* /app/data/ 2>/dev/null || true
    # 2. Check canonical parquet integrity on the persistent volume; force-overwrite if corrupted or missing
    python3 -c "import pandas as pd; df = pd.read_parquet('/app/data/canonical_pit_rebuilt.parquet'); assert len(df) >= 800" 2>/dev/null
    if [ $? -ne 0 ] && [ -f "/app/data_seed/canonical_pit_rebuilt.parquet" ]; then
        echo "⚠️ Corrupted or missing canonical_pit_rebuilt.parquet on volume. Self-healing from /app/data_seed..."
        cp -f /app/data_seed/canonical_pit_rebuilt.parquet /app/data/
        cp -f /app/data_seed/canonical_pit_rebuilt_meta.json /app/data/ 2>/dev/null || true
    fi
fi

while true; do
    echo "▶️ Launching app/main.py..."
    python3 app/main.py
    EXIT_CODE=$?
    
    echo "⚠️ main.py crashed or exited with code $EXIT_CODE."
    
    if [ $EXIT_CODE -eq 0 ]; then
        echo "🛑 Clean exit detected. Stopping supervisor loop."
        break
    fi
    
    echo "⏳ Respawning in 5 seconds..."
    sleep 5
done
