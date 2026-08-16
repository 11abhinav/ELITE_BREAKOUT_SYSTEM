"""
Unit tests for app/symbol_router.py (Capability-Aware Intelligent Symbol Router).
"""

import time
import pytest
from app.symbol_router import SymbolRouter, RoutingState, ProviderErrorCode

def test_permanent_fyers_failure_sets_upstox_only():
    router = SymbolRouter()
    # BSE:SIKA-EQ fails on Fyers with INVALID_INSTRUMENT for 1d
    router.record_result("BSE:SIKA-EQ", "1d", "fyers", is_success=False, err_code=ProviderErrorCode.INVALID_INSTRUMENT)
    
    assert router.get_route("BSE:SIKA-EQ", "1d") == RoutingState.UPSTOX_ONLY
    # Different timeframe for same symbol should remain LOAD_BALANCED unless independently learned
    assert router.get_route("BSE:SIKA-EQ", "15m") == RoutingState.LOAD_BALANCED

def test_transient_failure_remains_load_balanced():
    router = SymbolRouter()
    # XYZ fails on Fyers with TIMEOUT
    router.record_result("XYZ", "1d", "fyers", is_success=False, err_code=ProviderErrorCode.TIMEOUT)
    
    # Must NOT become UPSTOX_ONLY
    assert router.get_route("XYZ", "1d") == RoutingState.LOAD_BALANCED

def test_interval_specific_routing_nifty_scenario():
    router = SymbolRouter()
    # NIFTY 50 15m fails on Upstox with NOT_FOUND
    router.record_result("NIFTY 50", "15m", "upstox", is_success=False, err_code=ProviderErrorCode.NOT_FOUND)
    
    # 15m becomes FYERS_ONLY
    assert router.get_route("NIFTY 50", "15m") == RoutingState.FYERS_ONLY
    # 1d for NIFTY 50 remains LOAD_BALANCED!
    assert router.get_route("NIFTY 50", "1d") == RoutingState.LOAD_BALANCED

def test_self_healing_recovery_on_success():
    router = SymbolRouter()
    # Step 1: Learn sticky UPSTOX_ONLY
    router.record_result("ABC", "1d", "fyers", is_success=False, err_code=ProviderErrorCode.UNSUPPORTED_SYMBOL)
    assert router.get_route("ABC", "1d") == RoutingState.UPSTOX_ONLY
    
    # Step 2: Fyers later succeeds on revalidation
    router.record_result("ABC", "1d", "fyers", is_success=True)
    
    # Step 3: Restored to LOAD_BALANCED
    assert router.get_route("ABC", "1d") == RoutingState.LOAD_BALANCED

def test_concurrent_routing_updates():
    import threading
    router = SymbolRouter()
    
    def worker(i):
        sym = f"SYM_{i % 5}"
        router.record_result(sym, "1d", "fyers", is_success=False, err_code=ProviderErrorCode.NOT_FOUND)
        _ = router.get_route(sym, "1d")
        
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads: t.start()
    for t in threads: t.join()
    
    telemetry = router.get_telemetry_summary()
    assert telemetry["total_sticky_routes"] == 5
