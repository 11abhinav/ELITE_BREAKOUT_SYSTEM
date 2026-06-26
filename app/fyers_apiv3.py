# Minimal local stub for fyers_apiv3 used in tests when the real package is not installed.
# This stub provides the `fyersModel` namespace with SessionModel and FyersModel classes used by the app.

class fyersModel:
    class SessionModel:
        def __init__(self, client_id=None, secret_key=None, redirect_uri=None, response_type=None, grant_type=None):
            self.client_id = client_id
            self.secret_key = secret_key
            self.redirect_uri = redirect_uri
            self.response_type = response_type
            self.grant_type = grant_type
            self._token = None

        def generate_authcode(self):
            return "https://fyers.mock/auth"

        def set_token(self, token):
            self._token = token

        def generate_token(self):
            return {"access_token": "mock_access_token"}

    class FyersModel:
        def __init__(self, client_id=None, token=None, log_path=None, is_async=False):
            self.client_id = client_id
            self.token = token
            self.log_path = log_path
            self.is_async = is_async

        def history(self, data=None):
            # Real tests should patch fyers_auth.get_fyers_client to return a mock client.
            return {"s": "ok", "candles": []}

        def quotes(self, data=None):
            return {"s": "ok", "d": []}

