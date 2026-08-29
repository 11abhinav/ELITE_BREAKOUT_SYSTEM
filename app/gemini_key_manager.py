import os
import json
import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from config import DATA_DIR

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_blacklisted_gemini_keys_ram = {}
_active_gemini_key_ram = None
_gemini_keys_initialized = False

def _get_exhausted_gemini_keys_file():
    return os.path.join(DATA_DIR, "exhausted_gemini_keys.json")

def _init_gemini_key_state():
    """Initializes and restores Gemini 7-day blacklisted keys and sticky active key from DB & disk."""
    global _blacklisted_gemini_keys_ram, _active_gemini_key_ram, _gemini_keys_initialized
    with _cache_lock:
        if _gemini_keys_initialized:
            return
        restored_data = {}
        # 1. Load blacklisted keys from local disk
        try:
            fpath = _get_exhausted_gemini_keys_file()
            if os.path.exists(fpath):
                with open(fpath, 'r') as f:
                    restored_data = json.load(f)
        except Exception as e:
            logger.debug(f"Failed loading exhausted Gemini keys from file: {e}")

        # 2. Load blacklisted keys & sticky active key from PostgreSQL DB system_state
        try:
            from database import get_system_state
            db_json_str = get_system_state("exhausted_gemini_keys_v1")
            if db_json_str:
                db_data = json.loads(db_json_str)
                if isinstance(db_data, dict):
                    for k, v in db_data.items():
                        if k not in restored_data:
                            restored_data[k] = v
                        elif isinstance(v, dict) and isinstance(restored_data.get(k), dict):
                            if v.get("expires_at", "") > restored_data[k].get("expires_at", ""):
                                restored_data[k] = v

            # Load active sticky Gemini key from DB
            _active_gemini_key_ram = get_system_state("active_gemini_key_v1") or None
        except Exception as db_err:
            logger.debug(f"Failed loading Gemini proxy state from PostgreSQL DB: {db_err}")

        # Filter out expired keys (> 7 days old)
        now_dt = datetime.now(ZoneInfo('Asia/Kolkata'))
        now_iso = now_dt.isoformat()
        valid_data = {}
        for k, v in restored_data.items():
            if isinstance(v, dict):
                exp = v.get("expires_at", "")
                if exp and exp > now_iso:
                    valid_data[k] = v
            elif isinstance(v, str):
                valid_data[k] = {
                    "exhausted_at": now_iso,
                    "expires_at": (now_dt + timedelta(days=7)).isoformat(),
                    "legacy_date": v
                }

        _blacklisted_gemini_keys_ram = valid_data
        _gemini_keys_initialized = True
        if valid_data:
            logger.info(f"🛡️ [GEMINI STATE RESTORED] Restored {len(valid_data)} 7-day blacklisted Gemini key(s) from PostgreSQL DB system_state!")

def _is_gemini_key_exhausted(key: str) -> bool:
    """Checks if a Gemini API key is currently blacklisted (7-day TTL)."""
    if not key:
        return True
    try:
        _init_gemini_key_state()
        now_iso = datetime.now(ZoneInfo('Asia/Kolkata')).isoformat()
        with _cache_lock:
            entry = _blacklisted_gemini_keys_ram.get(key)
            if not entry:
                return False
            if isinstance(entry, dict):
                expires_at = entry.get("expires_at", "")
                if expires_at and now_iso < expires_at:
                    return True
                else:
                    _blacklisted_gemini_keys_ram.pop(key, None)
                    return False
        return False
    except Exception:
        return False

def mark_gemini_key_exhausted(key: str, reason: str = "Exhausted / Quota Limit Exceeded (7-day blacklist)"):
    """Blacklists a Gemini API key for 7 DAYS persistently in PostgreSQL DB & local disk."""
    if not key:
        return
    try:
        global _active_gemini_key_ram
        _init_gemini_key_state()
        now_dt = datetime.now(ZoneInfo('Asia/Kolkata'))
        expires_dt = now_dt + timedelta(days=7)
        now_iso = now_dt.isoformat()
        expires_iso = expires_dt.isoformat()

        entry = {
            "key": key,
            "exhausted_at": now_iso,
            "expires_at": expires_iso,
            "reason": reason
        }

        masked_key = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else key
        logger.warning(f"🚫 [7-DAY GEMINI BLACKLIST] Key [{masked_key}] marked EXHAUSTED & BLACKLISTED for 7 days (until {expires_dt.strftime('%Y-%m-%d %H:%M IST')}) persistently in PostgreSQL DB & disk.")

        with _cache_lock:
            _blacklisted_gemini_keys_ram[key] = entry
            if key == _active_gemini_key_ram:
                _active_gemini_key_ram = None
            ram_copy = dict(_blacklisted_gemini_keys_ram)

        # 1. Save local disk cache
        try:
            fpath = _get_exhausted_gemini_keys_file()
            with open(fpath, 'w') as f:
                json.dump(ram_copy, f, indent=2)
        except Exception as f_err:
            logger.debug(f"Failed to write exhausted Gemini keys to disk: {f_err}")

        # 2. Save persistently to PostgreSQL DB system_state
        try:
            from database import save_system_state
            save_system_state("exhausted_gemini_keys_v1", json.dumps(ram_copy, indent=2))
            if _active_gemini_key_ram is None:
                save_system_state("active_gemini_key_v1", "")
            logger.info(f"⚡ [POSTGRES DB BACKUP] 7-Day Gemini blacklist saved to PostgreSQL system_state for key [{masked_key}].")
        except Exception as db_err:
            logger.warning(f"⚠️ Failed to save 7-day Gemini blacklist to PostgreSQL DB: {db_err}")

    except Exception as e:
        logger.error(f"Failed to mark Gemini key exhausted for {key}: {e}")

def set_active_gemini_key(key: str):
    """Sets the confirmed active Gemini key and persists it to DB so it is reused until exhausted."""
    global _active_gemini_key_ram
    if not key or _is_gemini_key_exhausted(key):
        return
    with _cache_lock:
        if _active_gemini_key_ram != key:
            _active_gemini_key_ram = key
            try:
                from database import save_system_state
                save_system_state("active_gemini_key_v1", key)
                masked_key = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else key
                logger.info(f"🔑 [GEMINI ACTIVE KEY SAVED] Persisted sticky active Gemini key [{masked_key}] to PostgreSQL DB system_state.")
            except Exception:
                pass

def get_active_gemini_key() -> str:
    """
    Parse comma-separated GEMINI_API_KEY env var and return the active working key.
    Reuses the single active key sticky until it gets exhausted, then switches to next key.
    Persists active key in PostgreSQL DB across server restarts.
    """
    _init_gemini_key_state()
    keys_str = os.getenv("GEMINI_API_KEY", "")
    if not keys_str:
        return ""
    keys = [k.strip() for k in keys_str.split(',') if k.strip()]

    # 1. Use sticky active working key first if valid & not blacklisted
    global _active_gemini_key_ram
    if _active_gemini_key_ram and _active_gemini_key_ram in keys and not _is_gemini_key_exhausted(_active_gemini_key_ram):
        return _active_gemini_key_ram

    # 2. Find next non-exhausted key
    for k in keys:
        if not _is_gemini_key_exhausted(k):
            set_active_gemini_key(k)
            return k

    logger.warning("⚠️ [GEMINI] All provided GEMINI_API_KEY(s) are marked EXHAUSTED for the next 7 days!")
    return ""
