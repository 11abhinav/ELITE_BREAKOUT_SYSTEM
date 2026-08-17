import sys, os, logging

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("CLEAR_MAPPINGS")

from data_providers.fyers_mapping_utils import clear_all_symbol_mappings

if __name__ == "__main__":
    provider_arg = sys.argv[1] if len(sys.argv) > 1 else None
    logger.info(f"🧹 Clearing symbol_mappings table (Provider filter: {provider_arg or 'ALL'})...")
    success = clear_all_symbol_mappings(provider=provider_arg)
    if success:
        logger.info("✅ symbol_mappings table & RAM caches successfully cleared!")
    else:
        logger.error("❌ Failed to clear symbol_mappings table.")
