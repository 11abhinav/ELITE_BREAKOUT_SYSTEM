#!/bin/bash

echo "🚀 Starting Elite Breakout System Supervisor..."

export PYTHONUNBUFFERED=1
# Increase OS file descriptor limit to avoid [Errno 24] Too many open files
ulimit -n 65535 2>/dev/null || ulimit -n 4096 2>/dev/null || true
# Reduce glibc native memory fragmentation
export MALLOC_ARENA_MAX=2
export PYTHONPATH="/app:/app/app:$PYTHONPATH"

# Synchronously sync critical certified seed data before main.py boots
sync_seed_data() {
    if [ -d "/app/data_seed" ]; then
        echo "🔄 Running intelligent seed data synchronizer..."
        python3 /app/scripts/sync_seed_data.py || true
    fi
}
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
