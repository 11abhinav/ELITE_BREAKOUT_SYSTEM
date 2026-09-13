import os
import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
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
    """Initializes and restores Gemini 1-day blacklisted keys and sticky active key from DB & disk."""
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

        # [RULE 67 - FIX RATIONALE]: Gemini API quotas reset daily (every 24 hours / midnight UTC).
        # We enforce a strict 1-day (24h) blacklist instead of the previous 7-day lockout.
        # Any keys blacklisted >= 24h ago are automatically expired and unblocked immediately.
        # Any existing keys with a legacy 7-day expiration are capped to 24h from exhausted_at.
        now_dt = datetime.now(ZoneInfo('Asia/Kolkata'))
        now_iso = now_dt.isoformat()
        valid_data = {}
        cleaned_up = False
        for k, v in restored_data.items():
            if isinstance(v, dict):
                exhausted_at = v.get("exhausted_at", "")
                is_expired = False
                if exhausted_at:
                    try:
                        ex_dt = datetime.fromisoformat(exhausted_at)
                        if (now_dt - ex_dt) >= timedelta(days=1):
                            is_expired = True
                    except Exception:
                        pass
                
                exp = v.get("expires_at", "")
                if is_expired or (exp and exp <= now_iso):
                    cleaned_up = True
                    continue

                # Cap legacy 7-day expiration to 24 hours from exhausted_at
                if exhausted_at:
                    try:
                        ex_dt = datetime.fromisoformat(exhausted_at)
                        capped_exp = (ex_dt + timedelta(days=1)).isoformat()
                        if capped_exp < exp:
                            v["expires_at"] = capped_exp
                            exp = capped_exp
                            cleaned_up = True
                    except Exception:
                        pass

                if exp and exp > now_iso:
                    valid_data[k] = v
                else:
                    cleaned_up = True
            elif isinstance(v, str):
                cleaned_up = True

        _blacklisted_gemini_keys_ram = valid_data
        _gemini_keys_initialized = True
        if valid_data:
            logger.info(f"🛡️ [GEMINI STATE RESTORED] Restored {len(valid_data)} 1-day blacklisted Gemini key(s) from PostgreSQL DB system_state!")

        # Sync cleaned state back to disk & DB if any expired keys were pruned
        if cleaned_up or len(valid_data) != len(restored_data):
            try:
                fpath = _get_exhausted_gemini_keys_file()
                with open(fpath, 'w') as f:
                    json.dump(valid_data, f, indent=2)
            except Exception:
                pass
            try:
                from database import save_system_state
                save_system_state("exhausted_gemini_keys_v1", json.dumps(valid_data, indent=2))
                logger.info("⚡ [GEMINI STATE SYNC] Synced updated 1-day blacklist state to PostgreSQL DB system_state.")
            except Exception:
                pass

def _is_gemini_key_exhausted(key: str) -> bool:
    """Checks if a Gemini API key is currently blacklisted (1-day / 24h TTL)."""
    if not key:
        return True
    try:
        _init_gemini_key_state()
        now_dt = datetime.now(ZoneInfo('Asia/Kolkata'))
        now_iso = now_dt.isoformat()
        with _cache_lock:
            entry = _blacklisted_gemini_keys_ram.get(key)
            if not entry:
                return False
            if isinstance(entry, dict):
                # [RULE 67 - FIX RATIONALE]: Enforce strict 1-day (24h) TTL check from exhausted_at.
                exhausted_at = entry.get("exhausted_at", "")
                if exhausted_at:
                    try:
                        ex_dt = datetime.fromisoformat(exhausted_at)
                        if (now_dt - ex_dt) >= timedelta(days=1):
                            _blacklisted_gemini_keys_ram.pop(key, None)
                            return False
                    except Exception:
                        pass

                expires_at = entry.get("expires_at", "")
                if expires_at and now_iso < expires_at:
                    return True
                else:
                    _blacklisted_gemini_keys_ram.pop(key, None)
                    return False
        return False
    except Exception:
        return False

def revalidate_single_key_live(key: str) -> bool:
    """
    [RULE 67 - LIVE PRE-FLIGHT PROBE]:
    Directly queries Google's /v1beta/models endpoint to test whether the key has valid quota right now.
    Returns True if Google responds with HTTP 200 (quota available / key active), False otherwise.
    """
    if not key:
        return False
    try:
        import requests
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        headers = {
            "x-goog-api-key": key,
            "Content-Type": "application/json"
        }
        resp = requests.get(url, headers=headers, timeout=6)
        if resp.status_code == 200:
            return True
        elif resp.status_code == 429:
            logger.debug(f"Live key probe returned HTTP 429 (Resource Exhausted) for key: {key[:4]}...{key[-4:]}")
            return False
        else:
            logger.debug(f"Live key probe returned HTTP {resp.status_code} for key: {key[:4]}...{key[-4:]}")
            return False
    except Exception as e:
        logger.debug(f"Live key probe network exception for key {key[:4]}...{key[-4:]}: {e}")
        return False

def unblacklist_gemini_key(key: str, reason: str = "Live pre-flight probe succeeded (HTTP 200)"):
    """
    Removes a Gemini API key from the blacklist across RAM, local disk, and PostgreSQL DB.
    Restores the key to active status immediately.
    """
    if not key:
        return
    try:
        global _active_gemini_key_ram
        _init_gemini_key_state()
        with _cache_lock:
            if key in _blacklisted_gemini_keys_ram:
                _blacklisted_gemini_keys_ram.pop(key, None)
            _active_gemini_key_ram = key
            ram_copy = dict(_blacklisted_gemini_keys_ram)

        # 1. Update disk
        try:
            fpath = _get_exhausted_gemini_keys_file()
            with open(fpath, 'w') as f:
                json.dump(ram_copy, f, indent=2)
        except Exception:
            pass

        # 2. Update PostgreSQL DB
        try:
            from database import save_system_state
            save_system_state("exhausted_gemini_keys_v1", json.dumps(ram_copy, indent=2))
            save_system_state("active_gemini_key_v1", key)
        except Exception as db_err:
            logger.debug(f"Failed to persist unblacklisted key in DB: {db_err}")

        masked_key = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else key
        logger.info(f"✨ [GEMINI AUTO-RECOVERY] Key [{masked_key}] REMOVED FROM BLACKLIST and restored to ACTIVE ({reason})!")
    except Exception as e:
        logger.error(f"Failed to unblacklist Gemini key {key}: {e}")

def mark_gemini_key_exhausted(key: str, reason: str = "Exhausted / Quota Limit Exceeded (1-day blacklist)"):
    """Blacklists a Gemini API key for 1 DAY (24h) persistently in PostgreSQL DB & local disk."""
    if not key:
        return
    try:
        global _active_gemini_key_ram
        _init_gemini_key_state()
        now_dt = datetime.now(ZoneInfo('Asia/Kolkata'))
        expires_dt = now_dt + timedelta(days=1)
        now_iso = now_dt.isoformat()
        expires_iso = expires_dt.isoformat()

        entry = {
            "key": key,
            "exhausted_at": now_iso,
            "expires_at": expires_iso,
            "reason": reason
        }

        masked_key = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else key
        logger.warning(f"🚫 [1-DAY GEMINI BLACKLIST] Key [{masked_key}] marked EXHAUSTED & BLACKLISTED for 1 day (until {expires_dt.strftime('%Y-%m-%d %H:%M IST')}) persistently in PostgreSQL DB & disk.")

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
            logger.info(f"⚡ [POSTGRES DB BACKUP] 1-Day Gemini blacklist saved to PostgreSQL system_state for key [{masked_key}].")
        except Exception as db_err:
            logger.warning(f"⚠️ Failed to save 1-day Gemini blacklist to PostgreSQL DB: {db_err}")

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
    
    PRE-FLIGHT LIVE REVALIDATION PROTOCOL:
    1. Reuses sticky active working key if present and not blacklisted.
    2. Searches for any configured key that is not in the blacklist.
    3. PRE-FLIGHT PROBE: If all keys are currently blacklisted in cache/DB,
       actively probes Google's API for EVERY configured key.
       If any key succeeds (quota reset early, 24h passed, or temporary glitch resolved),
       it is instantly unblacklisted, saved to DB, and returned for usage.
    4. Only returns "" (triggering admin exhaustion alert) if ALL keys fail the live probe.
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

    # 2. Find next non-exhausted key in RAM/DB state
    for k in keys:
        if not _is_gemini_key_exhausted(k):
            set_active_gemini_key(k)
            return k

    # 3. PRE-FLIGHT LIVE REVALIDATION:
    # All keys are currently marked blacklisted. Before giving up and alerting admin,
    # actively test all keys against Google's API to see if any key has recovered.
    logger.info(f"🔍 [GEMINI PRE-FLIGHT PROBE] All {len(keys)} key(s) are blacklisted in cache. Probing Google API live before throwing admin alert...")
    for k in keys:
        masked = f"{k[:4]}...{k[-4:]}" if len(k) > 8 else k
        if revalidate_single_key_live(k):
            unblacklist_gemini_key(k, reason="Pre-flight live probe succeeded (HTTP 200)")
            return k
        else:
            logger.debug(f"Probe failed for key [{masked}]. Still exhausted.")

    logger.warning("⚠️ [GEMINI] All provided GEMINI_API_KEY(s) were live-tested and genuinely failed (all exhausted).")
    return ""
