import os
import json
import logging
from threading import Lock

logger = logging.getLogger(__name__)
_BSE_MAPPING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "bse_symbol_mappings.json")
_bse_mappings_cache = None
_mapping_lock = Lock()

def load_bse_mappings() -> dict[str, str]:
    global _bse_mappings_cache
    if _bse_mappings_cache is not None:
        return _bse_mappings_cache
    with _mapping_lock:
        if _bse_mappings_cache is not None:
            return _bse_mappings_cache
        try:
            if os.path.exists(_BSE_MAPPING_FILE):
                with open(_BSE_MAPPING_FILE, 'r') as f:
                    _bse_mappings_cache = json.load(f)
            else:
                _bse_mappings_cache = {}
        except Exception as e:
            logger.warning(f"Failed to load BSE symbol mappings: {e}")
            _bse_mappings_cache = {}
        return _bse_mappings_cache

def save_bse_mapping(original_sym: str, mapped_sym: str) -> None:
    mappings = load_bse_mappings()
    orig_clean = original_sym.strip().upper()
    mapped_clean = mapped_sym.strip().upper()
    if mappings.get(orig_clean) == mapped_clean:
        return
    with _mapping_lock:
        mappings[orig_clean] = mapped_clean
        try:
            os.makedirs(os.path.dirname(_BSE_MAPPING_FILE), exist_ok=True)
            with open(_BSE_MAPPING_FILE, 'w') as f:
                json.dump(mappings, f, indent=2)
            logger.info(f"💾 Persistent BSE fallback mapping saved: {orig_clean} -> {mapped_clean}")
        except Exception as e:
            logger.warning(f"Failed to save BSE symbol mappings: {e}")
