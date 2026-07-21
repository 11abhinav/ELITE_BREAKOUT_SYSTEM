#!/bin/bash

# ==============================================================================
# ELITE BREAKOUT SYSTEM - RAILWAY DATABASE MIGRATION SCRIPT
# ==============================================================================
# This script executes the exact strategy outlined in DATA_MIGRATION_PLAN_V3.md
# It securely migrates data from the Old Railway Database to the New Railway
# Database using optimized pg_dump (for config) and COPY (for large data).
# ==============================================================================

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 1. Config Tables (Small, exported via INSERTs for version control)
CONFIG_TABLES=(
    "symbol_mappings"
    "system_state"
    "bayesian_model_updates"
    "users"
    "push_subscriptions"
    "global_notifications"
    "telegram_queue"
)

# 2. Transactional Tables (Large, exported via CSV COPY)
TRANSACTIONAL_TABLES=(
    "alerts"
    "candidates"
    "system_logs"
    "wealth_score_history"
    "wealth_buy_alert"
    "trade_audit_log"
    "validation_history"
    "capital_history"
    "manual_portfolio"
)

# 3. Ignored Tables (Generated caches, ephemeral health data)
# parquet_cache, data_cache_metadata, data_fetch_health, fetch_errors, 
# scan_failures, scanner_health, ai_concall_cache_v3, promoter_pledge_cache

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE}  RAILWAY DATABASE MIGRATION ORCHESTRATOR  ${NC}"
echo -e "${BLUE}======================================================${NC}"

if [ -z "${OLD_DATABASE_URL:-}" ]; then
    read -sp "Enter OLD Railway Database URL (Postgres Connection String): " OLD_DATABASE_URL
    echo ""
fi

if [ -z "${NEW_DATABASE_URL:-}" ]; then
    read -sp "Enter NEW Railway Database URL (Postgres Connection String): " NEW_DATABASE_URL
    echo ""
fi

echo -e "\n${YELLOW}[!] Verifying connections...${NC}"
psql "$OLD_DATABASE_URL" -c "\q" || { echo -e "${RED}Failed to connect to OLD database.${NC}"; exit 1; }
psql "$NEW_DATABASE_URL" -c "\q" || { echo -e "${RED}Failed to connect to NEW database.${NC}"; exit 1; }
echo -e "${GREEN}[✓] Connections verified.${NC}"

WORK_DIR="migration_workspace_$(date +%s)"
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"
echo -e "\n${BLUE}Workspace created at: $WORK_DIR${NC}"

# ==========================================
# PHASE 1: SCHEMA MIGRATION
# ==========================================
echo -e "\n${YELLOW}[1/4] Migrating Database Schema...${NC}"
pg_dump "$OLD_DATABASE_URL" --schema-only -O -x > schema.sql
psql "$NEW_DATABASE_URL" -f schema.sql > /dev/null
echo -e "${GREEN}[✓] Schema migrated successfully.${NC}"

# ==========================================
# PHASE 2: CONFIGURATION TABLES (INSERT)
# ==========================================
echo -e "\n${YELLOW}[2/4] Migrating Configuration Tables (pg_dump inserts)...${NC}"
CONFIG_DUMP_ARGS=""
for TABLE in "${CONFIG_TABLES[@]}"; do
    CONFIG_DUMP_ARGS="$CONFIG_DUMP_ARGS -t $TABLE"
done

# We use --data-only and --column-inserts for small critical tables
pg_dump "$OLD_DATABASE_URL" $CONFIG_DUMP_ARGS --data-only --column-inserts > config_data.sql
psql "$NEW_DATABASE_URL" -f config_data.sql > /dev/null
echo -e "${GREEN}[✓] Configuration tables migrated successfully.${NC}"

# ==========================================
# PHASE 3: TRANSACTIONAL TABLES (COPY CSV)
# ==========================================
echo -e "\n${YELLOW}[3/4] Migrating Transactional Tables (COPY CSV)...${NC}"
for TABLE in "${TRANSACTIONAL_TABLES[@]}"; do
    echo -e "  -> Processing ${TABLE}..."
    
    # Check if table exists in source
    TABLE_EXISTS=$(psql "$OLD_DATABASE_URL" -tAc "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '$TABLE');")
    if [ "$TABLE_EXISTS" = "t" ]; then
        # Export
        psql "$OLD_DATABASE_URL" -c "\copy (SELECT * FROM $TABLE) TO '${TABLE}.csv' CSV HEADER;" > /dev/null
        
        # Import
        psql "$NEW_DATABASE_URL" -c "\copy $TABLE FROM '${TABLE}.csv' CSV HEADER;" > /dev/null
        echo -e "     ${GREEN}[✓] $TABLE migrated.${NC}"
    else
        echo -e "     ${YELLOW}[!] $TABLE does not exist in source. Skipping.${NC}"
    fi
done

# ==========================================
# PHASE 4: CLEANUP & NEXT STEPS
# ==========================================
echo -e "\n${YELLOW}[4/4] Cleaning up workspace...${NC}"
cd ..
rm -rf "$WORK_DIR"

echo -e "\n${BLUE}======================================================${NC}"
echo -e "${GREEN}✅ MIGRATION COMPLETED SUCCESSFULLY!${NC}"
echo -e "${BLUE}======================================================${NC}"
echo -e "Next steps:"
echo -e "1. Run the migration validator to compare row counts:"
echo -e "   python3 scripts/migration_validator.py"
echo -e "2. Deploy the application to the new environment."
echo -e "3. The 'daily_builder' will automatically rebuild the Parquet caches."
echo -e "======================================================"
