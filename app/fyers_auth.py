import os
import logging
from fyers_apiv3 import fyersModel
import config

logger = logging.getLogger(__name__)

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
        logger.error(f"Error generating Fyers login URL: {e}")
        raise

def save_access_token(auth_code: str) -> str:
    """Exchanges auth_code for access_token, saves to Postgres DB and locally."""
    try:
        session = get_session_model()
        session.set_token(auth_code)
        response = session.generate_token()
        
        if not response or "access_token" not in response:
            raise ValueError(f"Failed to generate access token from response: {response}")
            
        access_token = response["access_token"]
        
        # Save token to database to persist across container redeployments
        try:
            from database import save_system_state
            save_system_state("fyers_access_token", access_token)
        except Exception as db_err:
            logger.warning(f"Failed to save Fyers token to database: {db_err}")
        
        # Save token locally as fallback/cache
        token_path = config.FYERS_TOKEN_PATH
        os.makedirs(os.path.dirname(token_path), exist_ok=True)
        with open(token_path, "w") as f:
            f.write(access_token)
            
        logger.info(f"Fyers access token updated and saved to DB and {token_path}")
        return access_token
    except Exception as e:
        logger.error(f"Error saving Fyers access token: {e}")
        raise

def get_access_token() -> str:
    """Retrieves the access token from the database or local cache file."""
    # 1. Try reading from the database first (survives restarts)
    try:
        from database import get_system_state
        db_token = get_system_state("fyers_access_token")
        if db_token:
            # Sync to local file cache if missing or empty
            token_path = config.FYERS_TOKEN_PATH
            if not os.path.exists(token_path) or os.path.getsize(token_path) == 0:
                os.makedirs(os.path.dirname(token_path), exist_ok=True)
                with open(token_path, "w") as f:
                    f.write(db_token)
            return db_token
    except Exception as db_err:
        logger.warning(f"Failed to load Fyers token from database: {db_err}")

    # 2. Fallback to local file cache
    token_path = config.FYERS_TOKEN_PATH
    if not os.path.exists(token_path):
        return None
        
    try:
        with open(token_path, "r") as f:
            token = f.read().strip()
        return token if token else None
    except Exception as e:
        logger.error(f"Error reading Fyers access token file: {e}")
        return None


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
