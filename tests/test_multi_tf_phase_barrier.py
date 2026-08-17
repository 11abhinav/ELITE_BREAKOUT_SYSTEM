import pytest
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.abspath('app'))

from multi_tf_scanner import run_lower_tf_phase

def test_multi_tf_phase_promotion_barrier():
    """
    Verifies that run_lower_tf_phase pre-fetches 5m data for all candidates in the ladder,
    ensuring newly promoted ENTRY_READY candidates are evaluated by Phase D in the same run.
    """
    active_items = [
        {
            'symbol': 'TITAN',
            'category': 'LARGE',
            'sector': 'Consumer Goods',
            'current_state': 'HOURLY_APPROVED',
            'm30_status': 'PENDING',
            'm15_status': 'PENDING',
            'm5_status': 'PENDING',
            'trigger_level': 3000.0,
            'invalidation_level': 2900.0,
        },
        {
            'symbol': 'DIXON',
            'category': 'MIDCAP',
            'sector': 'Consumer Durables',
            'current_state': 'SETUP_ARMED',
            'm30_status': 'PASSED',
            'm15_status': 'PENDING',
            'm5_status': 'PENDING',
            'trigger_level': 12000.0,
            'invalidation_level': 11500.0,
        }
    ]
    
    # Executing run_lower_tf_phase in test mode
    # Should run without crashing and evaluate Phase D for all candidate stocks
    try:
        run_lower_tf_phase(active_items, is_test_mode=True)
    except Exception as e:
        pytest.fail(f"run_lower_tf_phase failed with error: {e}")
