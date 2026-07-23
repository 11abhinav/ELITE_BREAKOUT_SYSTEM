import pytest
from app.application_context import ApplicationContext
from app.session_context import SessionState, SessionContext, CachePolicy

def test_application_context_creates_session():
    """Verify ApplicationContext manages SessionContext correctly."""
    app_ctx = ApplicationContext()
    assert app_ctx.session_context is None
    
    session = app_ctx.create_session()
    assert session is not None
    assert app_ctx.session_context == session
    
    # Verify destroy
    app_ctx.destroy_session()
    assert app_ctx.session_context is None
    assert session.state == SessionState.DESTROYED


def test_session_state_transitions():
    """Verify the strict state machine enforces legal transitions."""
    session = SessionContext()
    assert session.state == SessionState.CREATED
    
    session.transition_to("WARMING")
    assert session.state == SessionState.WARMING
    
    session.transition_to("READY")
    assert session.state == SessionState.READY
    
    session.transition_to("MARKET_OPEN")
    assert session.state == SessionState.MARKET_OPEN
    
    session.transition_to("POST_MARKET")
    assert session.state == SessionState.POST_MARKET
    
    session.transition_to("SHUTTING_DOWN")
    assert session.state == SessionState.SHUTTING_DOWN
    
    session.transition_to("DESTROYED")
    assert session.state == SessionState.DESTROYED


def test_illegal_state_transitions_rejected():
    """Verify illegal transitions raise ValueError."""
    session = SessionContext()
    assert session.state == SessionState.CREATED
    
    with pytest.raises(ValueError, match="Illegal transition"):
        session.transition_to("MARKET_OPEN")  # Cannot skip WARMING and READY
        
    session.transition_to("WARMING")
    with pytest.raises(ValueError, match="Illegal transition"):
        session.transition_to("POST_MARKET")  # Cannot skip READY


def test_managers_initialized_correctly():
    """Verify SessionContext delegates to specialized managers correctly."""
    session = SessionContext()
    
    assert session.historical is not None
    assert session.historical.intraday is not None
    assert session.historical.daily is not None
    assert session.historical.delivery is not None
    
    assert session.indicators is not None
    assert session.market_regime is not None
    assert session.cache_manager is not None


def test_cache_policy_validation():
    """Verify CachePolicy objects are declarative and registered correctly."""
    session = SessionContext()
    
    # Check that managers correctly registered their policies
    policies = session.cache_manager.managed_policies
    assert "intraday" in policies
    assert "daily" in policies
    assert "indicators" in policies
    
    intraday_policy = policies["intraday"]
    assert isinstance(intraday_policy, CachePolicy)
    assert intraday_policy.owner == "HistoricalDataManager.IntradayStore"
    assert intraday_policy.persistence == "SESSION"
    assert intraday_policy.refresh_policy == "EVERY_5_MIN"
    assert intraday_policy.expiration_policy == "CONSUMER_DRIVEN"
    assert intraday_policy.consumer_count == 0
