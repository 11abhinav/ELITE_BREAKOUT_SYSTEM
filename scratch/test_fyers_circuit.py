import time
from unittest.mock import MagicMock, patch

from app.data_providers.fyers_fetcher import FyersFetcher, _fyers_circuit_breaker


def run_test():
    # Ensure circuit breaker reset
    _fyers_circuit_breaker.reset()
    fetcher = FyersFetcher()

    # Mock client that always returns an error response
    mock_client = MagicMock()
    mock_client.history.return_value = {"s": "error", "message": "Bad request", "code": 400}

    # Note: fyers_fetcher imports fyers_auth as a top-level module name -> patch that symbol
    with patch('fyers_auth.get_fyers_client', return_value=mock_client):
        # Trigger failures up to threshold
        threshold = _fyers_circuit_breaker.failure_threshold
        for i in range(threshold):
            df = fetcher.get_ohlcv('RELIANCE', '1d', '30d')
            # response should be None or empty
            if i < threshold - 1:
                assert df is None
            else:
                # After reaching threshold, circuit should open and return None
                assert df is None

        # Circuit breaker should now be open
        assert not _fyers_circuit_breaker.is_available()

        # Next call should return immediately (None) without invoking client.history
        mock_client.history.reset_mock()
        df = fetcher.get_ohlcv('RELIANCE', '1d', '30d')
        assert df is None
        mock_client.history.assert_not_called()

    print('Fyers circuit breaker test passed')


if __name__ == '__main__':
    run_test()

