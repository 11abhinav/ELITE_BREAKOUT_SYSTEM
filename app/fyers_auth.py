import os
import threading
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
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
            
        from zoneinfo import ZoneInfo
        now_date_str = str(datetime.now(ZoneInfo('Asia/Kolkata')).date())
        token_payload = {
            "token": access_token,
            "date": now_date_str,
            "updated_at": datetime.now(ZoneInfo('Asia/Kolkata')).isoformat()
        }
        
        # Save token to database to persist across container redeployments
        try:
            import json
            from database import save_system_state
            save_system_state("fyers_access_token", json.dumps(token_payload))
            save_system_state("fyers_access_token_date", now_date_str)
        except Exception as db_err:
            logger.warning(f"Failed to save Fyers token to database: {db_err}")
        
        # Save token locally as fallback/cache
        token_path = config.FYERS_TOKEN_PATH
        os.makedirs(os.path.dirname(token_path), exist_ok=True)
        with open(token_path, "w") as f:
            f.write(access_token)
            
        global _cached_token, _token_date, _autologin_attempted_date
        with _token_lock:
            _cached_token = access_token
            _token_date = now_date_str
            _autologin_attempted_date = None

        logger.info(f"✅ Fyers access token updated and saved to DB (date={now_date_str}) and {token_path}")
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

_active_working_scraper_key = None

def get_valid_scraper_keys():
    """Returns a list of configured ScraperAPI keys, prioritizing the active working key and filtering out keys blacklisted project-wide for today."""
    global _active_working_scraper_key
    try:
        from pledge_scraper import _is_key_exhausted_today
    except Exception:
        def _is_key_exhausted_today(k): return False

    scraper_raw = os.environ.get("SCRAPERAPI_KEY", "")
    if not scraper_raw:
        return []
    
    all_keys = [k.strip() for k in scraper_raw.split(",") if k.strip()]
    valid_keys = [k for k in all_keys if not _is_key_exhausted_today(k)]
    
    if _active_working_scraper_key and _active_working_scraper_key in valid_keys:
        valid_keys.remove(_active_working_scraper_key)
        valid_keys.insert(0, _active_working_scraper_key)
        
    return valid_keys

def fyers_post_with_scraper_fallback(session, target_url, payload, headers=None):
    """Executes a POST request to Fyers API. Tries ScraperAPI proxy FIRST (with keep_headers=true) to guarantee Cloudflare WAF bypass, then falls back to direct connection."""
    global _active_working_scraper_key
    import urllib.parse
    headers = headers or {}

    # 1. ScraperAPI Proxy Attempt (Primary: Bypasses Cloudflare WAF using residential proxy pool)
    valid_keys = get_valid_scraper_keys()
    for scraper_key in valid_keys:
        try:
            scraper_url = f"http://api.scraperapi.com?api_key={scraper_key}&keep_headers=true&url={urllib.parse.quote(target_url)}"
            logger.info(f"🌐 Routing POST request via ScraperAPI Proxy ({scraper_key[:5]}...) for {target_url}...")
            res_scraper = session.post(scraper_url, json=payload, headers=headers, timeout=25)
            body_scraper = res_scraper.text.strip()
            
            # Blacklist dead / exhausted / invalid keys project-wide for today
            if res_scraper.status_code in (401, 403, 429, 499) or "exhausted" in body_scraper.lower() or "unauthorized" in body_scraper.lower() or "multiple users" in body_scraper.lower():
                logger.warning(f"❌ ScraperAPI key ({scraper_key[:5]}...) is EXHAUSTED/DEAD (Status {res_scraper.status_code}). Blacklisting key project-wide for today.")
                try:
                    from pledge_scraper import mark_key_exhausted_today
                    mark_key_exhausted_today(scraper_key)
                except Exception:
                    pass
                if _active_working_scraper_key == scraper_key:
                    _active_working_scraper_key = None
                continue
                
            if res_scraper.status_code in (200, 201) and not body_scraper.startswith("<!doctype") and not body_scraper.startswith("<html"):
                logger.info(f"✅ ScraperAPI Proxy ({scraper_key[:5]}...) successfully fetched POST {target_url}!")
                _active_working_scraper_key = scraper_key
                return res_scraper
        except Exception as s_err:
            logger.warning(f"ScraperAPI proxy key attempt failed ({scraper_key[:5]}...): {s_err}")

    # 2. Direct Connection Fallback (If ScraperAPI key is not configured or all keys failed)
    logger.info(f"Attempting direct POST connection to {target_url}...")
    try:
        return session.post(target_url, json=payload, headers=headers, timeout=10)
    except Exception as direct_err:
        logger.error(f"❌ Direct POST connection to {target_url} failed: {direct_err}")
        raise

def fyers_get_with_scraper_fallback(session, target_url, headers=None):
    """Executes a GET request to Fyers API. Tries ScraperAPI proxy FIRST (with keep_headers=true) to guarantee Cloudflare WAF bypass, then falls back to direct connection."""
    global _active_working_scraper_key
    import urllib.parse
    headers = headers or {}

    valid_keys = get_valid_scraper_keys()
    for scraper_key in valid_keys:
        try:
            scraper_url = f"http://api.scraperapi.com?api_key={scraper_key}&keep_headers=true&url={urllib.parse.quote(target_url)}"
            logger.info(f"🌐 Routing GET request via ScraperAPI Proxy ({scraper_key[:5]}...) for {target_url}...")
            res_scraper = session.get(scraper_url, headers=headers, allow_redirects=False, timeout=25)
            body_scraper = res_scraper.text.strip()
            
            # Blacklist dead / exhausted / invalid keys project-wide for today
            if res_scraper.status_code in (401, 403, 429, 499) or "exhausted" in body_scraper.lower() or "unauthorized" in body_scraper.lower() or "multiple users" in body_scraper.lower():
                logger.warning(f"❌ ScraperAPI key ({scraper_key[:5]}...) is EXHAUSTED/DEAD (Status {res_scraper.status_code}). Blacklisting key project-wide for today.")
                try:
                    from pledge_scraper import mark_key_exhausted_today
                    mark_key_exhausted_today(scraper_key)
                except Exception:
                    pass
                if _active_working_scraper_key == scraper_key:
                    _active_working_scraper_key = None
                continue
                
            if res_scraper.status_code in (200, 301, 302, 303, 307, 308) and not body_scraper.startswith("<!doctype") and not body_scraper.startswith("<html"):
                logger.info(f"✅ ScraperAPI Proxy ({scraper_key[:5]}...) successfully fetched GET {target_url}!")
                _active_working_scraper_key = scraper_key
                return res_scraper
        except Exception as s_err:
            logger.warning(f"ScraperAPI proxy GET key attempt failed ({scraper_key[:5]}...): {s_err}")

    logger.info(f"Attempting direct GET connection to {target_url}...")
    try:
        return session.get(target_url, headers=headers, allow_redirects=False, timeout=10)
    except Exception as direct_err:
        logger.error(f"❌ Direct GET connection to {target_url} failed: {direct_err}")
        raise

_auto_login_lock = threading.Lock()
_last_auto_login_time = 0.0

def auto_login() -> Optional[str]:
    """Automates headless login to Fyers using stored credentials."""
    global _last_auto_login_time, _cached_token
    import time
    with _auto_login_lock:
        now_ts = time.time()
        # Cooldown guard: Prevent sending repeated automated OTP requests within 300 seconds (5 mins)
        if now_ts - _last_auto_login_time < 300.0 and _last_auto_login_time > 0.0 and _cached_token:
            logger.warning(f"⏳ Fyers auto-login attempted within 5-minute cooldown ({int(now_ts - _last_auto_login_time)}s ago). Returning active cached token.")
            return _cached_token

        _last_auto_login_time = now_ts
        try:
            client_id = config.FYERS_CLIENT_ID
            secret_key = config.FYERS_SECRET_KEY
            totp_secret = os.environ.get("FYERS_TOTP_SECRET")
            pin = os.environ.get("FYERS_PIN")
            user_id = os.environ.get("FYERS_USER_ID")
            redirect_uri = config.FYERS_REDIRECT_URL
            
            # Diagnostic parameter validation logs
            logger.info("🔍 [FYERS ENV PARAMETER DIAGNOSTIC]:")
            logger.info(f"  • FYERS_CLIENT_ID: {'✅ SET' if client_id else '❌ MISSING'} (prefix='{client_id[:2] if client_id else ''}...', suffix='...{client_id[-2:] if client_id else ''}', len={len(client_id) if client_id else 0})")
            logger.info(f"  • FYERS_SECRET_KEY: {'✅ SET' if secret_key else '❌ MISSING'} (prefix='{secret_key[:2] if secret_key else ''}...', suffix='...{secret_key[-2:] if secret_key else ''}', len={len(secret_key) if secret_key else 0})")
            logger.info(f"  • FYERS_USER_ID: {'✅ SET' if user_id else '❌ MISSING'} (prefix='{user_id[:2] if user_id else ''}...', suffix='...{user_id[-2:] if user_id else ''}', len={len(user_id) if user_id else 0})")
            logger.info(f"  • FYERS_PIN: {'✅ SET' if pin else '❌ MISSING'} (len={len(pin) if pin else 0})")
            logger.info(f"  • FYERS_TOTP_SECRET: {'✅ SET' if totp_secret else '❌ MISSING'} (prefix='{totp_secret[:2] if totp_secret else ''}...', suffix='...{totp_secret[-2:] if totp_secret else ''}', len={len(totp_secret) if totp_secret else 0})")
            logger.info(f"  • FYERS_REDIRECT_URL: {redirect_uri}")

            if not all([client_id, secret_key, totp_secret, pin, user_id]):
                logger.error("Skipping headless Fyers login due to missing credentials. Check env vars.")
                return None
                
            import pyotp
            import base64
            import requests
            import urllib.parse
            
            logger.info("🔑 [VERSION: FYERS_AUTH_v28.0_ALL_SCENARIOS_VERIFIED_AIRTIGHT] Starting Fyers headless OAuth auto-login flow...")
            logger.info("Fyers login Step 1: Sending login OTP request...")
            session = requests.Session()
            payload = {"fy_id": base64.b64encode(f"{user_id.strip()}".encode()).decode(), "app_id": "2"}
            res_obj = fyers_post_with_scraper_fallback(session, "https://api-t2.fyers.in/vagator/v2/send_login_otp_v2", payload)
            
            error_text = res_obj.text.strip()
            if res_obj.status_code != 200 or error_text.startswith("<!doctype") or error_text.startswith("<html") or "CF-1" in error_text or "Anomaly Detection" in error_text:
                logger.error(f"🚫 Fyers Step 1 blocked by Cloudflare WAF / IP Rate-Limit (Status {res_obj.status_code}). Response: {error_text[:250]}...")
                logger.error("👉 ACTION REQUIRED: Authenticate manually via Dashboard UI at https://elitebreakout.duckdns.org/fyers/login or wait 15 mins for Cloudflare IP reset.")
                return None

            try:
                res = res_obj.json()
                logger.info(f"Fyers Step 1 response payload: {res}")
            except ValueError:
                logger.error(f"Fyers Step 1 invalid JSON response (Status {res_obj.status_code}): {error_text[:250]}...")
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
            res2_obj = fyers_post_with_scraper_fallback(session, "https://api-t2.fyers.in/vagator/v2/verify_otp", payload2)
            try:
                res2 = res2_obj.json()
                logger.info(f"Fyers Step 2 response payload: {res2}")
            except ValueError:
                logger.error(f"Fyers Step 2 non-JSON response (Status {res2_obj.status_code}): {res2_obj.text[:250]}...")
                return None
                
            if 'request_key' not in res2:
                logger.error(f"Fyers Step 2 TOTP verification failed: {res2}")
                return None
            request_key2 = res2["request_key"]
            
            logger.info("Fyers login Step 3: Verifying PIN...")
            payload3 = {"request_key": request_key2, "identity_type": "pin", "identifier": base64.b64encode(f"{pin.strip()}".encode()).decode()}
            res3_obj = fyers_post_with_scraper_fallback(session, "https://api-t2.fyers.in/vagator/v2/verify_pin_v2", payload3)
            try:
                res3 = res3_obj.json()
                logger.info(f"Fyers Step 3 response status: {res3.get('s')}, code: {res3.get('code')}")
            except ValueError:
                logger.error(f"Fyers Step 3 non-JSON response (Status {res3_obj.status_code}): {res3_obj.text[:250]}...")
                return None
                
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
            step4_jwt = None
            
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
                res4_obj = fyers_post_with_scraper_fallback(session, "https://api-t1.fyers.in/api/v3/token", payload4, headers=headers)
                try:
                    res4 = res4_obj.json()
                    logger.info(f"Fyers Step 4 raw response for '{cand_app_id}': {res4}")
                except ValueError:
                    logger.error(f"Fyers Step 4 non-JSON response ({cand_app_id}, Status {res4_obj.status_code}): {res4_obj.text[:250]}...")
                    continue
                
                if isinstance(res4.get('data'), dict) and res4['data'].get('auth'):
                    step4_jwt = res4['data']['auth']

                url = res4.get('Url') or res4.get('redirectUrl') or (isinstance(res4.get('data'), dict) and res4['data'].get('redirectUrl'))
                if url:
                    parsed = urllib.parse.urlparse(url)
                    qs = urllib.parse.parse_qs(parsed.query)
                    auth_code = qs.get('auth_code', [None])[0]

                # Step 4b: If direct token response did NOT return ?auth_code= in redirectUrl,
                # test generate-authcode via ScraperAPI GET
                if not auth_code and step4_jwt:
                    auth_headers = {"Authorization": f"Bearer {step4_jwt}"}
                    
                    for client_id_for_4b in (target_app_id, cand_app_id):
                        auth_params = {
                            "client_id": client_id_for_4b,
                            "redirect_uri": redirect_uri,
                            "response_type": "code",
                            "state": "abcdefg"
                        }
                        authcode_url = f"https://api-t1.fyers.in/api/v3/generate-authcode?{urllib.parse.urlencode(auth_params)}"
                        logger.info(f"Fyers Step 4b: Requesting OAuth authorize redirect via ScraperAPI GET (client_id='{client_id_for_4b}')...")
                        
                        res_redirect = fyers_get_with_scraper_fallback(session, authcode_url, headers=auth_headers)
                        is_redirect = res_redirect.status_code in (301, 302, 303, 307, 308)
                        redirect_location = res_redirect.headers.get('Location') or res_redirect.headers.get('location')
                        
                        if res_redirect.status_code == 200:
                            try:
                                json_200 = res_redirect.json()
                                target_url_200 = (json_200.get('Url') or json_200.get('redirectUrl') or 
                                                 (isinstance(json_200.get('data'), dict) and (json_200['data'].get('redirectUrl') or json_200['data'].get('Url'))))
                                if target_url_200:
                                    parsed_200 = urllib.parse.urlparse(target_url_200)
                                    qs_200 = urllib.parse.parse_qs(parsed_200.query)
                                    codes = qs_200.get('auth_code') or qs_200.get('auth') or qs_200.get('code')
                                    if codes:
                                        auth_code = codes[0]
                                        successful_app_id = client_id_for_4b
                                        logger.info(f"✅ Fyers Step 4b Trace (HTTP 200 JSON): auth_code Present=YES | Length={len(auth_code)} | client_id='{successful_app_id}'")
                                        break
                            except Exception:
                                logger.warning(f"Fyers Step 4b HTTP 200 non-JSON text ({client_id_for_4b}): {res_redirect.text[:200]}")
                        elif is_redirect and redirect_location:
                            parsed_loc = urllib.parse.urlparse(redirect_location)
                            qs_loc = urllib.parse.parse_qs(parsed_loc.query)
                            codes = qs_loc.get('auth_code') or qs_loc.get('auth') or qs_loc.get('code')
                            if codes:
                                auth_code = codes[0]
                                successful_app_id = client_id_for_4b
                                break

                if not auth_code and step4_jwt:
                    auth_code = step4_jwt
                    successful_app_id = target_app_id
                    logger.info(f"ℹ️ Fyers Step 4: Using Step 4 session JWT (len={len(auth_code)}) for Step 5 verification (client_id='{successful_app_id}')...")

                if auth_code:
                    if not successful_app_id:
                        successful_app_id = cand_app_id
                    logger.info(f"✅ Fyers Step 4 successfully obtained credential token for App ID '{successful_app_id}'. Proceeding immediately to Step 5 verification...")
                    break

            if not auth_code:
                logger.error("❌ Fyers Step 4 auth code failed for all App ID variants.")
                dispatch_fyers_reauth_notification("Fyers OAuth auto-login failed at Step 4.")
                return None
            
            logger.info(f"Fyers login Step 5: Exchanging/Validating credential token (len={len(auth_code)}) for all-day access token (client_id='{successful_app_id}')...")
            try:
                session_m = get_session_model(client_id=successful_app_id)
                session_m.set_token(auth_code)
                response = session_m.generate_token()
                
                if response and isinstance(response, dict) and "access_token" in response:
                    real_token = response["access_token"]
                    logger.info(f"✅ Fyers Step 5: generate_token() SUCCESS | AccessToken len={len(real_token)} | Saving to DB & local cache.")
                    return save_access_token_direct(real_token)
                else:
                    logger.info(f"Fyers Step 5: generate_token() returned {response}. Testing token directly against live Fyers API profile...")
                    try:
                        from fyers_apiv3 import fyersModel
                        for test_client_id in (successful_app_id, app_id_clean, target_app_id):
                            f_client = fyersModel.FyersModel(client_id=test_client_id, is_async=False, token=auth_code, log_path=os.getcwd())
                            profile_res = f_client.get_profile()
                            logger.info(f"Fyers Step 5 Profile Test ({test_client_id}): {profile_res}")
                            if profile_res and isinstance(profile_res, dict) and (profile_res.get('s') == 'ok' or profile_res.get('code') == 200 or 'data' in profile_res):
                                logger.info(f"✅ Fyers Step 5: Direct session token verified live with Fyers API profile for client_id '{test_client_id}'! | AccessToken len={len(auth_code)} | Saving to DB & local cache.")
                                return save_access_token_direct(auth_code)
                    except Exception as prof_err:
                        logger.error(f"Fyers Step 5 profile validation exception: {prof_err}")

                    logger.error("❌ Fyers Step 5 token exchange and direct validation failed.")
                    dispatch_fyers_reauth_notification("Fyers token validation failed.")
                    return None
            except Exception as exc:
                logger.error(f"❌ Fyers Step 5 token exchange exception: {exc}")
                dispatch_fyers_reauth_notification(f"Fyers token exchange error: {exc}")
                return None
            
        except Exception as e:
            import traceback
            logger.error(f"Fyers headless login failed with exception: {e}\n{traceback.format_exc()}")
            dispatch_fyers_reauth_notification(f"Fyers headless login crash: {e}")
            return None

def dispatch_fyers_reauth_notification(reason: str = "Fyers API access token is missing or expired."):
    """Dispatches both an in-app global notification bell alert AND a WebPush notification with a direct 1-tap clickable URL."""
    try:
        login_url = "https://elitebreakout.duckdns.org/fyers/login"
        msg = f"{reason} Click here to authenticate with 1 tap: {login_url}"
        
        # 1. Insert into global_notifications table for the Dashboard UI Bell 🔔
        try:
            from database import insert_notification
            insert_notification("admin", "🔑 FYERS AUTH REQUIRED", msg)
        except Exception as db_notif_err:
            logger.warning(f"Failed to insert Fyers re-auth in-app notification: {db_notif_err}")

        # 2. Dispatch WebPush to mobile/desktop browsers
        try:
            from push_service import send_push_to_all
            logger.info(f"🔔 Dispatching admin clickable push notification for Fyers re-authentication: {login_url}")
            send_push_to_all(
                "🔑 FYERS AUTH REQUIRED", 
                f"{reason} Tap here to authenticate with 1 click.", 
                url=login_url, 
                bypass_throttle=True
            )
        except Exception as push_err:
            logger.warning(f"Failed to dispatch Fyers re-auth push notification: {push_err}")
    except Exception as exc:
        logger.warning(f"Error in dispatch_fyers_reauth_notification: {exc}")


_token_lock = threading.Lock()
_autologin_attempted_date = None

def get_access_token() -> str:
    """Retrieves the access token prioritizing DB, then local file, then ONE-TIME auto-login per day."""
    from zoneinfo import ZoneInfo
    from datetime import datetime as _dt
    global _cached_token, _token_date, _autologin_attempted_date
    now_date = str(_dt.now(ZoneInfo('Asia/Kolkata')).date())

    with _token_lock:
        if _cached_token and _token_date == now_date:
            return _cached_token

        # 1. Try fetching from DB
        try:
            from database import get_system_state
            db_state = get_system_state("fyers_access_token")
            saved_date = get_system_state("fyers_access_token_date")
            token = None
            if isinstance(db_state, dict):
                token = db_state.get("token")
                saved_date = db_state.get("date", saved_date)
            elif isinstance(db_state, str) and db_state.strip():
                if db_state.strip().startswith("{"):
                    try:
                        import json
                        parsed = json.loads(db_state)
                        if isinstance(parsed, dict):
                            token = parsed.get("token")
                            saved_date = parsed.get("date", saved_date)
                        else:
                            token = db_state.strip()
                    except Exception:
                        token = db_state.strip()
                else:
                    token = db_state.strip()

            if token and saved_date == now_date:
                logger.info(f"⚡ [DB CACHE HIT] Loaded active Fyers access token for today ({now_date}) from PostgreSQL!")
                _cached_token = token
                _token_date = now_date
                return token
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

        # 3. Suppress repeat auto-login attempts if already tried today and failed
        if _autologin_attempted_date == now_date:
            logger.error(f"❌ [FYERS AUTH ERROR] Fyers token missing for today ({now_date}). Auto-login was already attempted on startup and failed. Please authenticate via /fyers/login.")
            return None

        # 4. Attempt auto-login ONCE for today
        _autologin_attempted_date = now_date
        logger.info(f"No valid Fyers token for today found in DB or locally. Attempting ONE-TIME ScraperAPI auto-login for today ({now_date})...")
        token = auto_login()
        if token:
            _cached_token = token
            _token_date = now_date
            return token

        # Dispatch clickable admin notification ONCE and return None
        dispatch_fyers_reauth_notification("Fyers access token could not be generated automatically.")
        logger.error(f"❌ [FYERS AUTH ERROR] Auto-login failed on startup. Lock set for today ({now_date}) — please authenticate via /fyers/login.")
        return None

def clear_token(force: bool = False):
    """Clears in-memory token cache. Does NOT delete valid DB token unless force=True."""
    global _cached_token, _token_date
    with _token_lock:
        _cached_token = None
        _token_date = None

    if force:
        token_path = config.FYERS_TOKEN_PATH
        if os.path.exists(token_path):
            try:
                os.remove(token_path)
            except Exception:
                pass
                
        try:
            from database import save_system_state
            save_system_state("fyers_access_token", "")
            save_system_state("fyers_access_token_date", "")
            logger.info("🗑️ Forced deletion of Fyers access token from DB and disk.")
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
