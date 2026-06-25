import pytest
import json
from app.dashboard_server import app

@pytest.fixture
def client():
    # Use testing mode for the app
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_get_watchlist_returns_200(client, mocker):
    # Mock the database call
    mocker.patch(
        "app.dashboard_server.database.get_active_breakout_watchlist",
        return_value=[{"symbol": "RELIANCE", "current_state": "TRACKING"}]
    )
    
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    
    response = client.get('/api/breakout_watchlist')
    assert response.status_code == 200
    
    data = json.loads(response.data)
    assert isinstance(data, dict)
    assert data["status"] == "success"
    
    watchlist = data["data"]
    assert isinstance(watchlist, list)
    assert len(watchlist) == 1
    assert watchlist[0]["symbol"] == "RELIANCE"

def test_get_alerts_returns_200(client, mocker):
    mocker.patch(
        "app.dashboard_server.database.get_todays_alerts",
        return_value=[{"symbol": "TCS", "breakout_type": "1h"}]
    )
    
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    
    response = client.get('/api/todays_alerts')
    assert response.status_code == 200
    
    data = json.loads(response.data)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["symbol"] == "TCS"

def test_api_health_endpoint(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert json.loads(response.data)["status"] == "ok"
