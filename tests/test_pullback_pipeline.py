import pytest
import numpy as np
import pandas as pd
from decimal import Decimal
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../app')))

from core_enums import CandidateState
from pullback_pipeline import run_pullback_pipeline

def test_pipeline_import():
    # Verify module loads and functions exist
    import pullback_pipeline
    assert hasattr(pullback_pipeline, 'run_pullback_pipeline')
    assert hasattr(pullback_pipeline, 'start')

if __name__ == "__main__":
    pytest.main(["-v", __file__])
