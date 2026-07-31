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

def get_session_model(client_id: str = None) -> fyersModel.SessionModel:
    """Helper to initialize SessionModel using client credentials."""
    cid = (client_id or config.FYERS_CLIENT_ID or "").strip()
    if not cid or not config.FYERS_SECRET_KEY:
        raise ValueError("FYERS_CLIENT_ID or FYERS_SECRET_KEY is not configured in environment/config.")
    
    return fyersModel.SessionModel(
        client_id=cid,
        secret_key=config.FYERS_SECRET_KEY,
        redirect_uri=config.FYERS_REDIRECT_URL,
        response_type="code",
        grant_type="authorization_code"
    )

def get_token_app_id(token: str) -> Optional[str]:
    """Helper to decode JWT payload without verification to extract app_id or client_id claim."""
    if not token or not token.startswith("eyJ"):
        return None
    try:
        import base64, json
        parts = token.split(".")
        if len(parts) >= 2:
            padding = "=" * (4 - len(parts[1]) % 4)
            payload_bytes = base64.urlsafe_b64decode(parts[1] + padding)
            payload = json.loads(payload_bytes.decode('utf-8'))
            return payload.get("app_id") or payload.get("client_id")
    except Exception:
        pass
    return None

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
        response = session.generate_token()
        
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
            
            # Diagnostic parameter validation logs (masking sensitive values for security)
            def _mask(val):
                if not val:
                    return "❌ MISSING"
                val_str = str(val).strip()
                if len(val_str) <= 4:
                    return f"✅ SET (len={len(val_str)})"
                return f"✅ SET (prefix='{val_str[:2]}...', suffix='...{val_str[-2:]}', len={len(val_str)})"

            logger.info("🔍 [FYERS ENV PARAMETER DIAGNOSTIC]:")
            logger.info(f"  • FYERS_CLIENT_ID: {_mask(client_id)}")
            logger.info(f"  • FYERS_SECRET_KEY: {_mask(secret_key)}")
            logger.info(f"  • FYERS_USER_ID: {_mask(user_id)}")
            logger.info(f"  • FYERS_PIN: {_mask(pin)}")
            logger.info(f"  • FYERS_TOTP_SECRET: {_mask(totp_secret)}")
            logger.info(f"  • FYERS_REDIRECT_URL: {redirect_uri or '❌ MISSING'}")

            if totp_secret:
                try:
                    import pyotp
                    _test_totp = pyotp.TOTP(totp_secret.strip()).now()
                    logger.info(f"  ✅ FYERS_TOTP_SECRET valid base32 key (generated test TOTP len={len(_test_totp)})")
                except Exception as totp_err:
                    logger.error(f"  ❌ FYERS_TOTP_SECRET IS INVALID BASE32 KEY: {totp_err}")

            if client_id:
                if not client_id.strip().endswith("-100"):
                    logger.warning(f"  ⚠️ FYERS_CLIENT_ID ('{client_id}') does NOT end with '-100' suffix required by Fyers API v3.")

            if not all([client_id, secret_key, totp_secret, pin, user_id]):
                logger.error(f"Skipping headless Fyers login due to missing credentials. Check env vars: CLIENT_ID={bool(client_id)}, SECRET={bool(secret_key)}, TOTP={bool(totp_secret)}, PIN={bool(pin)}, USER_ID={bool(user_id)}")
                return None
                
            import pyotp
            import base64
            import requests
            import urllib.parse
            
            logger.info("Fyers login Step 1: Sending login OTP request...")
            session = requests.Session()
            payload = {"fy_id": base64.b64encode(f"{user_id.strip()}".encode()).decode(), "app_id": "2"}
            res_obj = session.post("https://api-t2.fyers.in/vagator/v2/send_login_otp_v2", json=payload)
            try:
                res = res_obj.json()
                logger.info(f"Fyers Step 1 response payload: {res}")
            except ValueError:
                error_text = res_obj.text
                if "CF-1" in error_text or "Anomaly Detection" in error_text:
                    logger.error("Fyers auto-login blocked by Cloudflare (Datacenter IP detected). You MUST authenticate manually via the Dashboard UI today.")
                else:
                    logger.error(f"Fyers Step 1 failed, invalid JSON response: {error_text[:200]}...")
                return None
            
            if 'request_key' not in res:
                logger.error(f"Fyers Step 1 failed: {res}")
                return None
            request_key = res["request_key"]
            
            logger.info("Fyers login Step 2: Verifying TOTP...")
            try:
                totp = pyotp.TOTP(totp_secret.strip()).now()
                logger.info(f"Generated TOTP for Step 2: {totp[:2]}****")
            except Exception as e:
                logger.error(f"Fyers TOTP generation failed. Check if FYERS_TOTP_SECRET is valid base32: {e}")
                return None
                
            payload2 = {"request_key": request_key, "otp": totp}
            res2 = session.post("https://api-t2.fyers.in/vagator/v2/verify_otp", json=payload2).json()
            logger.info(f"Fyers Step 2 response payload: {res2}")
            
            if 'request_key' not in res2:
                logger.error(f"Fyers Step 2 TOTP verification failed: {res2}")
                return None
            request_key = res2["request_key"]
            
            logger.info("Fyers login Step 3: Verifying PIN...")
            payload3 = {"request_key": request_key, "identity_type": "pin", "identifier": base64.b64encode(f"{pin.strip()}".encode()).decode()}
            res3 = session.post("https://api-t2.fyers.in/vagator/v2/verify_pin_v2", json=payload3).json()
            logger.info(f"Fyers Step 3 response status: {res3.get('s')}, code: {res3.get('code')}")
            
            if 'data' not in res3 or 'access_token' not in res3.get('data', {}):
                logger.error(f"Fyers Step 3 PIN verification failed: {res3}")
                return None
            auth_token = res3["data"]["access_token"]
            
            target_app_id = client_id.strip()
            if not target_app_id.endswith("-100"):
                target_app_id = f"{target_app_id}-100"

            app_id_clean = client_id.split("-")[0] if "-" in client_id else client_id
            
            headers = {"Authorization": f"Bearer {auth_token}"}
            auth_code = None
            successful_app_id = None
            
            # Try full client_id first (e.g. M0SD1EXNYU-100) so token app_id claim matches FyersModel client_id
            for cand_app_id in (target_app_id, app_id_clean):
                logger.info(f"Fyers login Step 4: Requesting auth code for App ID '{cand_app_id}'...")
                payload4 = {
                    "fyers_id": user_id.strip(),
                    "app_id": cand_app_id,
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
                logger.info(f"Fyers Step 4 raw response for '{cand_app_id}': {res4}")
                
                url = res4.get('Url') or res4.get('redirectUrl') or (isinstance(res4.get('data'), dict) and res4['data'].get('redirectUrl'))
                if url:
                    parsed = urllib.parse.urlparse(url)
                    qs = urllib.parse.parse_qs(parsed.query)
                    auth_code = qs.get('auth_code', [None])[0]

                # Step 4b: If direct token response did NOT return ?auth_code= in redirectUrl,
                # follow the standard OAuth 2.0 authorize redirect endpoint to get the real auth_code
                if not auth_code:
                    step4_jwt = isinstance(res4.get('data'), dict) and res4['data'].get('auth')
                    auth_headers = {"Authorization": f"Bearer {step4_jwt}"} if step4_jwt else headers
                    
                    auth_params = {
                        "client_id": cand_app_id,
                        "redirect_uri": redirect_uri,
                        "response_type": "code",
                        "state": "abcdefg"
                    }
                    authcode_url = f"https://api-t1.fyers.in/api/v3/generate-authcode?{urllib.parse.urlencode(auth_params)}"
                    logger.info(f"Fyers Step 4b: Requesting OAuth authorize redirect via GET {authcode_url}...")
                    
                    res_redirect = session.get(authcode_url, headers=auth_headers, allow_redirects=False)
                    is_redirect = res_redirect.status_code in (301, 302, 303, 307, 308)
                    redirect_location = res_redirect.headers.get('Location') or res_redirect.headers.get('location')
                    
                    logger.info(f"Fyers Step 4b Trace: Status={res_redirect.status_code} | IsRedirect={is_redirect} | LocationHeader={'YES' if redirect_location else 'NO'}")
                    
                    if is_redirect and redirect_location:
                        parsed_loc = urllib.parse.urlparse(redirect_location)
                        qs_loc = urllib.parse.parse_qs(parsed_loc.query)
                        qs_frag = urllib.parse.parse_qs(parsed_loc.fragment)
                        
                        logger.info(f"Fyers Step 4b Location breakdown: host={parsed_loc.netloc}, path={parsed_loc.path}, query_keys={list(qs_loc.keys())}, frag_keys={list(qs_frag.keys())}")
                        
                        codes = (qs_loc.get('auth_code') or qs_loc.get('auth') or qs_loc.get('code') or
                                 qs_frag.get('auth_code') or qs_frag.get('auth') or qs_frag.get('code'))
                        if codes:
                            auth_code = codes[0]
                            logger.info(f"Fyers Step 4b Trace: auth_code Present=YES | Length={len(auth_code)} | ParamMatched=YES")
                        else:
                            logger.error(f"Fyers Step 4b Location missing auth_code. Full query='{parsed_loc.query}', fragment='{parsed_loc.fragment}'")

                if auth_code:
                    successful_app_id = cand_app_id
                    logger.info(f"✅ Fyers Step 4 successfully obtained real OAuth auth_code for App ID '{cand_app_id}'. Proceeding immediately to Step 5 exchange...")
                    break

            if not auth_code:
                logger.error("❌ Fyers Step 4 auth code failed for all App ID variants.")
                return None
            
            logger.info(f"Fyers login Step 5: Exchanging OAuth auth_code (len={len(auth_code)}) for all-day access token via generate_token() (client_id='{successful_app_id}')...")
            try:
                session_m = get_session_model(client_id=successful_app_id)
                session_m.set_token(auth_code)
                response = session_m.generate_token()
                
                if response and isinstance(response, dict) and "access_token" in response:
                    real_token = response["access_token"]
                    logger.info(f"✅ Fyers Step 5: generate_token() SUCCESS | AccessToken len={len(real_token)} | Saving to DB & local cache.")
                    return save_access_token_direct(real_token)
                else:
                    logger.error(f"❌ Fyers Step 5 generate_token() exchange failed: {response}.")
                    return None
            except Exception as exc:
                logger.error(f"❌ Fyers Step 5 generate_token() exchange exception: {exc}")
                return None
            
        except Exception as e:
            import traceback
            logger.error(f"Fyers headless login failed with exception: {e}\n{traceback.format_exc()}")
            return None


_token_lock = threading.Lock()

def get_access_token() -> str:
    """Retrieves the access token prioritizing DB, then local file, then auto-login."""
    from zoneinfo import ZoneInfo
    from datetime import datetime as _dt
    global _cached_token, _token_date
    now_date = str(_dt.now(ZoneInfo('Asia/Kolkata')).date())

    with _token_lock:
        if _cached_token and _token_date == now_date:
            return _cached_token

        # 1. Try fetching from DB
        try:
            from database import get_system_state
            db_token = get_system_state("fyers_access_token")
            saved_date = get_system_state("fyers_access_token_date")
            if db_token and saved_date == now_date:
                _cached_token = db_token
                _token_date = now_date
                return db_token
        except Exception as e:
            logger.warning(f"Error fetching Fyers token from DB: {e}")

        # 2. Try fetching from local file
        token_path = config.FYERS_TOKEN_PATH
        if os.path.exists(token_path):
            try:
                mtime = os.path.getmtime(token_path)
                mtime_date = str(_dt.fromtimestamp(mtime, ZoneInfo('Asia/Kolkata')).date())
                if mtime_date == now_date:
                    with open(token_path, "r") as f:
                        token = f.read().strip()
                        if token:
                            _cached_token = token
                            _token_date = now_date
                            return token
            except Exception as e:
                logger.warning(f"Error reading Fyers token file: {e}")

        # 3. Try auto_login if no valid token for today
        logger.info("No valid Fyers token for today found in DB or locally. Attempting headless auto-login...")
        token = auto_login()
        if token:
            _cached_token = token
            _token_date = now_date
            return token

        return None

def clear_token():
    """Clears cached token locally and from database on authentication failures."""
    global _cached_token, _token_date
    with _token_lock:
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
    
    # Align client_id to token app_id claim if decoded, defaulting to config.FYERS_CLIENT_ID
    target_client_id = config.FYERS_CLIENT_ID
    token_app = get_token_app_id(token)
    if token_app:
        if token_app.endswith("-100"):
            target_client_id = token_app
        elif f"{token_app}-100" == config.FYERS_CLIENT_ID:
            target_client_id = config.FYERS_CLIENT_ID
        else:
            target_client_id = token_app

    client = fyersModel.FyersModel(
        client_id=target_client_id,
        token=token,
        log_path=log_path,
        is_async=False
    )
    return client
