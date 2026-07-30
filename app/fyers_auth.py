import os
import threading
import logging
from datetime import datetime
from typing import Optional
from fyers_apiv3 import fyersModel
import config

logger = logging.getLogger(__name__)

# Cache for the current day
_cached_token = None
_token_date = None

def get_session_model() -> fyersModel.SessionModel:
    """Helper to initialize SessionModel using client credentials."""
    if not config.FYERS_CLIENT_ID or not config.FYERS_SECRET_KEY:
        raise ValueError("FYERS_CLIENT_ID or FYERS_SECRET_KEY is not configured in environment/config.")
    
    return fyersModel.SessionModel(
        client_id=config.FYERS_CLIENT_ID,
        secret_key=config.FYERS_SECRET_KEY,
        redirect_uri=config.FYERS_REDIRECT_URL,
        response_type="code",
        grant_type="authorization_code"
    )

def get_login_url() -> str:
    """Generates the Fyers authorization URL."""
    try:
        session = get_session_model()
        return session.generate_authcode()
    except Exception as e:
        logger.exception(f"Error generating Fyers login URL")
        raise

def save_access_token_direct(access_token: str) -> str:
    """Saves access_token to Postgres DB and locally without exchanging."""
    try:
        if not access_token:
            return None
            
        # Save token to database to persist across container redeployments
        try:
            from database import save_system_state
            from zoneinfo import ZoneInfo
            save_system_state("fyers_access_token", access_token)
            save_system_state("fyers_access_token_date", str(datetime.now(ZoneInfo('Asia/Kolkata')).date()))
        except Exception as db_err:
            logger.warning(f"Failed to save Fyers token to database: {db_err}")
        
        # Save token locally as fallback/cache
        token_path = config.FYERS_TOKEN_PATH
        os.makedirs(os.path.dirname(token_path), exist_ok=True)
        with open(token_path, "w") as f:
            f.write(access_token)
            
        logger.info(f"✅ Fyers access token updated and saved to DB and {token_path}")
        return access_token
    except Exception as e:
        logger.warning(f"Error saving Fyers access token: {e}")
        return None

def is_direct_access_token(token_str: str) -> bool:
    """Inspects unverified JWT payload to check if sub == 'access_token'."""
    if not token_str or not isinstance(token_str, str) or not token_str.startswith("eyJ"):
        return False
    try:
        import base64, json
        parts = token_str.split(".")
        if len(parts) >= 2:
            payload_b64 = parts[1]
            rem = len(payload_b64) % 4
            if rem > 0:
                payload_b64 += "=" * (4 - rem)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            sub = str(payload.get("sub", ""))
            return sub == "access_token" or payload.get("token_type") == "access_token"
    except Exception:
        pass
    return False

def save_access_token(auth_code: str) -> str:
    """Exchanges auth_code for access_token, saves to Postgres DB and locally."""
    try:
        if not auth_code:
            return None
            
        # 1. Fast Path: If auth_code is ALREADY a direct access token JWT (sub == 'access_token'), save directly
        if is_direct_access_token(auth_code):
            logger.info("Fyers login Step 5: Direct access token JWT detected, saving directly...")
            return save_access_token_direct(auth_code)

        # 2. Exchange auth_code with Fyers SessionModel to obtain real access_token
        logger.info("Fyers login Step 5: Exchanging authorization code for access token via SessionModel...")
        session = get_session_model()
        session.set_token(auth_code)
        
        response = None
        for exchange_attempt in range(2):
            try:
                response = session.generate_token()
                if response and isinstance(response, dict) and "access_token" in response:
                    break
            except Exception as ex_err:
                logger.warning(f"Fyers token exchange attempt {exchange_attempt + 1} exception: {ex_err}")
            time.sleep(1.0)
        
        if response and isinstance(response, dict) and "access_token" in response:
            access_token = response["access_token"]
            logger.info("✅ Fyers authorization code successfully exchanged for access token.")
            return save_access_token_direct(access_token)
        else:
            logger.warning(f"Fyers token exchange returned unexpected payload: {response}")
            return None
    except Exception as e:
        logger.warning(f"Error saving Fyers access token: {e}")
        return None

_auto_login_lock = threading.Lock()
_last_auto_login_time = 0.0

def auto_login() -> Optional[str]:
    """Automates headless login to Fyers using stored credentials."""
    global _last_auto_login_time, _cached_token
    import time
    with _auto_login_lock:
        now_ts = time.time()
        if now_ts - _last_auto_login_time < 60.0 and _cached_token:
            logger.info("⏳ Skipping duplicate Fyers auto-login (ran within 60s cooldown). Returning active cached token.")
            return _cached_token

        _last_auto_login_time = now_ts
        try:
            client_id = config.FYERS_CLIENT_ID
            secret_key = config.FYERS_SECRET_KEY
            totp_secret = os.environ.get("FYERS_TOTP_SECRET")
            pin = os.environ.get("FYERS_PIN")
            user_id = os.environ.get("FYERS_USER_ID")
            redirect_uri = config.FYERS_REDIRECT_URL
            
            if not all([client_id, secret_key, totp_secret, pin, user_id]):
                logger.error(f"Skipping headless Fyers login due to missing credentials. Check env vars: CLIENT_ID={bool(client_id)}, SECRET={bool(secret_key)}, TOTP={bool(totp_secret)}, PIN={bool(pin)}, USER_ID={bool(user_id)}")
                return None
                
            import pyotp
            import base64
            import requests
            import urllib.parse
            
            logger.info("Fyers login Step 1: Sending login OTP request...")
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://mweb.fyers.in",
                "Referer": "https://mweb.fyers.in/"
            })
            payload = {"fy_id": base64.b64encode(f"{user_id}".encode()).decode(), "app_id": "2"}
            
            res = None
            for step1_attempt in range(3):
                res_obj = session.post("https://api-t2.fyers.in/vagator/v2/send_login_otp_v2", json=payload)
                try:
                    res = res_obj.json()
                except ValueError:
                    error_text = res_obj.text
                    if "CF-1" in error_text or "Anomaly Detection" in error_text:
                        logger.error("Fyers auto-login blocked by Cloudflare (Datacenter IP detected). You MUST authenticate manually via the Dashboard UI today.")
                    else:
                        logger.error(f"Fyers Step 1 failed, invalid JSON response: {error_text[:200]}...")
                    return None
                
                # Check for Cloudflare Error 1015 (rate limit)
                if isinstance(res, dict) and (res.get("error_code") == 1015 or res.get("status") == 429 or "rate limit" in str(res.get("title", "")).lower()):
                    retry_wait = int(res.get("retry_after", 30))
                    logger.warning(f"⏳ [Cloudflare Error 1015] Fyers Step 1 rate-limited. Waiting {retry_wait}s before retry (attempt {step1_attempt + 1}/3)...")
                    time.sleep(retry_wait)
                    continue
                break
            
            if not res or 'request_key' not in res:
                logger.error(f"Fyers Step 1 failed: {res}")
                return None
            request_key = res["request_key"]
            
            logger.info("Fyers login Step 2: Verifying TOTP...")
            res2 = None
            for totp_attempt in range(2):
                try:
                    totp = pyotp.TOTP(totp_secret).now()
                except Exception as e:
                    logger.error(f"Fyers TOTP generation failed. Check if FYERS_TOTP_SECRET is valid base32: {e}")
                    return None
                    
                payload2 = {"request_key": request_key, "otp": totp}
                res2 = session.post("https://api-t2.fyers.in/vagator/v2/verify_otp", json=payload2).json()
                if isinstance(res2, dict) and 'request_key' in res2:
                    break
                logger.warning(f"Fyers Step 2 TOTP attempt {totp_attempt + 1} failed: {res2}. Retrying in 1s...")
                time.sleep(1.0)
            
            if not res2 or 'request_key' not in res2:
                logger.error(f"Fyers Step 2 TOTP verification failed: {res2}")
                return None
            request_key = res2["request_key"]
            
            logger.info("Fyers login Step 3: Verifying PIN...")
            payload3 = {"request_key": request_key, "identity_type": "pin", "identifier": base64.b64encode(f"{pin}".encode()).decode()}
            res3 = session.post("https://api-t2.fyers.in/vagator/v2/verify_pin_v2", json=payload3).json()
            
            if 'data' not in res3 or 'access_token' not in res3.get('data', {}):
                logger.error(f"Fyers Step 3 PIN verification failed: {res3}")
                return None
            auth_token = res3["data"]["access_token"]
            
            app_id_clean = client_id.split("-")[0] if "-" in client_id else client_id
            logger.info(f"Fyers login Step 4: Requesting auth code for App ID '{app_id_clean}' (original: '{client_id}')...")
            headers = {"Authorization": f"Bearer {auth_token}"}
            payload4 = {
                "fyers_id": user_id,
                "app_id": app_id_clean,
                "redirect_uri": redirect_uri,
                "appType": "100",
                "code_challenge": "",
                "state": "abcdefg",
                "scope": "",
                "nonce": "",
                "response_type": "code",
                "create_cookie": True
            }
            res4 = session.post("https://api-t1.fyers.in/api/v3/token", json=payload4, headers=headers).json()
            logger.info(f"Fyers Step 4 raw response status: {res4.get('s')}, code: {res4.get('code')}")
            
            url = res4.get('Url') or res4.get('redirectUrl') or (isinstance(res4.get('data'), dict) and res4['data'].get('redirectUrl'))
            auth_code = None
            if url:
                parsed = urllib.parse.urlparse(url)
                qs = urllib.parse.parse_qs(parsed.query)
                if 'auth_code' in qs:
                    auth_code = qs['auth_code'][0]
                elif 'auth' in qs:
                    auth_code = qs['auth'][0]

            if not auth_code and isinstance(res4.get('data'), dict):
                auth_code = res4['data'].get('auth') or res4['data'].get('auth_code')

            if not auth_code:
                logger.error(f"Fyers Step 4 auth code failed. Payload response: {res4}")
                return None
            
            logger.info("Fyers login Step 5: Generating access token...")
            return save_access_token(auth_code)
            
        except Exception as e:
            import traceback
            logger.error(f"Fyers headless login failed with exception: {e}\n{traceback.format_exc()}")
            return None


_token_lock = threading.Lock()

def get_access_token() -> str:
    """Retrieves the access token prioritizing DB, then local file, then auto-login."""
    from zoneinfo import ZoneInfo
    global _cached_token, _token_date
    now_date = datetime.now(ZoneInfo('Asia/Kolkata')).date()
    now_str = str(now_date)
    
    if _cached_token and _token_date == now_date:
        return _cached_token

    with _token_lock:
        if _cached_token and _token_date == now_date:
            return _cached_token

        token = None
        token_path = config.FYERS_TOKEN_PATH
        
        # 1. Try reading from the database first (survives restarts)
        try:
            from database import get_system_state
            db_token = get_system_state("fyers_access_token")
            db_token_date = get_system_state("fyers_access_token_date")
            
            # If we have a token and its date is today's date
            if db_token and db_token_date == now_str:
                # Sync to local file cache if missing or empty
                if not os.path.exists(token_path) or os.path.getsize(token_path) == 0:
                    os.makedirs(os.path.dirname(token_path), exist_ok=True)
                    with open(token_path, "w") as f:
                        f.write(db_token)
                
                _cached_token = db_token
                _token_date = now_date
                return db_token
        except Exception as db_err:
            logger.warning(f"Failed to load Fyers token from database: {db_err}")

        # 2. Check if local file exists and was modified today (fallback if DB fails)
        if os.path.exists(token_path) and os.path.getsize(token_path) > 0:
            mtime = os.path.getmtime(token_path)
            file_date = datetime.fromtimestamp(mtime).date()
            if file_date == now_date:
                try:
                    with open(token_path, "r") as f:
                        token = f.read().strip()
                    if token:
                        _cached_token = token
                        _token_date = now_date
                        return token
                except Exception as e:
                    logger.warning(f"Error reading Fyers access token file: {e}")

        # 3. Try auto_login if no valid token for today
        logger.info("No valid Fyers token for today found in DB or locally. Attempting headless auto-login...")
        token = auto_login()
    if token:
        _cached_token = token
        _token_date = now_date
        return token

    # 4. Fallback: just try to use local file even if old (last resort)
    if os.path.exists(token_path):
        try:
            with open(token_path, "r") as f:
                token = f.read().strip()
            if token:
                logger.warning("Using EXPIRED Fyers token from local file as absolute fallback.")
                return token
        except Exception:
            pass

    return None

def clear_token():
    """Clears the cached and database Fyers token to force a re-login."""
    global _cached_token, _token_date
    _cached_token = None
    _token_date = None
    
    token_path = config.FYERS_TOKEN_PATH
    if os.path.exists(token_path):
        try:
            os.remove(token_path)
        except Exception:
            pass
            
    try:
        from database import save_system_state
        save_system_state("fyers_access_token", "")
    except Exception as e:
        logger.error(f"Failed to clear token from DB: {e}")
        
def get_fyers_client() -> fyersModel.FyersModel:
    """Initializes and returns an authenticated FyersModel client."""
    token = get_access_token()
    if not token:
        logger.warning("Fyers access token is not available. Please authenticate via /fyers/login.")
        return None
        
    if not config.FYERS_CLIENT_ID:
        logger.error("FYERS_CLIENT_ID is not configured.")
        return None
        
    # Use config data directory for Fyers logs
    log_path = os.path.join(config.DATA_DIR, "fyers_logs")
    os.makedirs(log_path, exist_ok=True)
    
    client = fyersModel.FyersModel(
        client_id=config.FYERS_CLIENT_ID,
        token=token,
        log_path=log_path,
        is_async=False
    )
    return client
