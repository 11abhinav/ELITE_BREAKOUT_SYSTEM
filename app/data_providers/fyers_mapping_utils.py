import os
import json
import logging
from threading import Lock

logger = logging.getLogger(__name__)
_FYERS_MAPPING_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "fyers_symbol_mappings.json")
_fyers_mappings_cache = None
_mapping_lock = Lock()

def load_fyers_mappings():
    global _fyers_mappings_cache
    if _fyers_mappings_cache is not None:
        return _fyers_mappings_cache
    with _mapping_lock:
        if _fyers_mappings_cache is not None:
            return _fyers_mappings_cache
        try:
            if os.path.exists(_FYERS_MAPPING_FILE):
                with open(_FYERS_MAPPING_FILE, 'r') as f:
                    _fyers_mappings_cache = json.load(f)
            else:
                _fyers_mappings_cache = {}
        except Exception as e:
            logger.warning(f"Failed to load Fyers symbol mappings: {e}")
            _fyers_mappings_cache = {}
        return _fyers_mappings_cache

def save_fyers_mapping(original_sym: str, mapped_sym: str):
    mappings = load_fyers_mappings()
    if mappings.get(original_sym) == mapped_sym:
        return
    with _mapping_lock:
        mappings[original_sym] = mapped_sym
        try:
            os.makedirs(os.path.dirname(_FYERS_MAPPING_FILE), exist_ok=True)
            with open(_FYERS_MAPPING_FILE, 'w') as f:
                json.dump(mappings, f)
        except Exception as e:
            logger.warning(f"Failed to save Fyers symbol mappings: {e}")

def remove_fyers_mapping(original_sym: str):
    mappings = load_fyers_mappings()
    if original_sym not in mappings:
        return
    with _mapping_lock:
        if original_sym in mappings:
            del mappings[original_sym]
            try:
                with open(_FYERS_MAPPING_FILE, 'w') as f:
                    json.dump(mappings, f)
            except Exception as e:
                logger.warning(f"Failed to remove Fyers symbol mapping: {e}")
