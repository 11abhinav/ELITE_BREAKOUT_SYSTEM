import inspect
import dataclasses
from typing import get_type_hints, get_args, get_origin, List

from app.validation import (
    ValidationEngine,
    DataQualityReport,
    ValidationResult,
    BaseValidator,
    BaseScoreCalculator,
    ValidationFailure,
    FailureCode
)
from app.validation.validators.price_validator import PriceValidator
from app.validation.scoring.price_score import PriceScoreCalculator

def test_data_quality_report_is_frozen():
    """Ensure that the final output of the validation engine cannot be mutated."""
    assert dataclasses.is_dataclass(DataQualityReport), "DataQualityReport must be a dataclass"
    assert DataQualityReport.__dataclass_params__.frozen is True, "DataQualityReport must be @dataclass(frozen=True)"

def test_validation_engine_is_stateless():
    """
    Ensure the validation engine does not accumulate state across runs.
    The only allowed state variables are its injected dependencies (validator, score_calculator).
    """
    engine = ValidationEngine(PriceValidator(), PriceScoreCalculator())
    
    allowed_keys = {"validator", "score_calculator"}
    actual_keys = set(engine.__dict__.keys())
    
    unauthorized_state = actual_keys - allowed_keys
    assert not unauthorized_state, f"ValidationEngine has unauthorized state variables: {unauthorized_state}. It must be stateless."

def test_validator_inheritance():
    """Ensure all classes acting as validators inherit from BaseValidator."""
    # Currently we only have PriceValidator, but we can check it
    validators = [PriceValidator]
    for cls in validators:
        assert issubclass(cls, BaseValidator), f"{cls.__name__} must inherit from BaseValidator"

def test_score_calculator_inheritance():
    """Ensure all classes acting as score calculators inherit from BaseScoreCalculator."""
    calculators = [PriceScoreCalculator]
    for cls in calculators:
        assert issubclass(cls, BaseScoreCalculator), f"{cls.__name__} must inherit from BaseScoreCalculator"

def test_validation_result_strict_types():
    """Ensure ValidationResult strictly uses Lists of ValidationFailure, not raw strings."""
    hints = get_type_hints(ValidationResult)
    
    for field_name in ["schema_failures", "business_failures", "historical_failures"]:
        field_type = hints.get(field_name)
        assert field_type is not None, f"ValidationResult is missing '{field_name}' type hint"
        
        # In Python 3.9+, List[ValidationFailure] origin is list, args is (ValidationFailure,)
        origin = get_origin(field_type)
        args = get_args(field_type)
        
        assert origin is list or origin is List, f"ValidationResult.{field_name} must be a list"
        assert args == (ValidationFailure,), f"ValidationResult.{field_name} must contain ValidationFailure, got {args}"

def test_validation_failure_valid_code():
    """Ensure ValidationFailure objects use strict Enum codes."""
    failure = ValidationFailure(code=FailureCode.SCH001, severity="CRITICAL", message="Test")
    assert isinstance(failure.code, FailureCode), "ValidationFailure must use FailureCode enum"


def test_config_invariants():
    """Safety guard: verify core strategy configuration constants do not silently drift."""
    from app.config import (
        EXIT_PROFILES,
        SCANNER_EXIT_PROFILE,
        ALERT_COOLDOWN_MINUTES,
        SCANNER_MAX_ALERTS,
        MIN_NATURAL_RR,
        ADAPTIVE_TARGET_CAPS,
    )
    
    # 1. Exit profile allocations
    assert EXIT_PROFILES["BALANCED"] == {"t1": 30, "t2": 40, "t3": 30}
    assert EXIT_PROFILES["AGGRESSIVE"] == {"t1": 20, "t2": 30, "t3": 50}
    assert EXIT_PROFILES["CONSERVATIVE"] == {"t1": 25, "t2": 50, "t3": 25}
    
    assert SCANNER_EXIT_PROFILE["EOD"] == "BALANCED"
    assert SCANNER_EXIT_PROFILE["MULTI_TF"] == "AGGRESSIVE"
    assert SCANNER_EXIT_PROFILE["REVERSAL"] == "CONSERVATIVE"
    assert SCANNER_EXIT_PROFILE["PULLBACK"] == "BALANCED"
    
    # 2. Scanner Cooldowns
    assert ALERT_COOLDOWN_MINUTES["MULTI_TF"] == 240
    assert ALERT_COOLDOWN_MINUTES["EOD"] == 1440
    assert ALERT_COOLDOWN_MINUTES["REVERSAL"] == 10080
    assert ALERT_COOLDOWN_MINUTES["PULLBACK"] == 10080
    
    # 3. Max Alerts
    assert SCANNER_MAX_ALERTS["MULTI_TF"] == 15
    assert SCANNER_MAX_ALERTS["EOD"] == 10
    
    # 4. Min Natural RR Floor
    assert MIN_NATURAL_RR["EOD"] == 2.0
    assert MIN_NATURAL_RR["MULTI_TF"] == 1.5
    
    # 5. Adaptive Target Caps - 9 Regimes
    expected_regimes = {
        "STRONG_BULL", "WEAK_BULL", "BULL",
        "BEAR", "WEAK_BEAR", "STRONG_BEAR",
        "SIDEWAYS", "RANGEBOUND", "NEUTRAL"
    }
    assert set(ADAPTIVE_TARGET_CAPS.keys()) == expected_regimes


def test_symbol_normalization_invariants():
    """Safety guard: verify Fyers and YFinance symbol normalization contracts."""
    from app.data_providers.fyers_fetcher import FyersFetcher
    fetcher = FyersFetcher()
    
    # Bare BSE symbol should auto-append -EQ
    assert fetcher._normalize_symbol("BSE:NSDL") == "BSE:NSDL-EQ"
    assert fetcher._normalize_symbol("NSDL") == "BSE:NSDL-EQ"
    assert fetcher._normalize_symbol("RELIANCE") == "NSE:RELIANCE-EQ"
    assert fetcher._normalize_symbol("NSE:STOCK-BE") == "NSE:STOCK-BE"
    assert fetcher._normalize_symbol("SENSEX") == "BSE:SENSEX-INDEX"
    assert fetcher._normalize_symbol("NIFTY 50") == "NSE:NIFTY50-INDEX"
    assert fetcher._normalize_symbol("BANKNIFTY") == "NSE:NIFTYBANK-INDEX"

def test_provider_fetcher_isolation_and_all_symbols_contract():
    """
    ARCHITECTURE INVARIANT:
    All symbol categories (Indices, NSE Spot, BSE, SME, BE Series, ASM/GSM, IPOs)
    MUST be supported across both Fyers and YFinance fetchers.
    All provider-specific customizations (suffixes, index mapping, resolution codes)
    MUST be strictly encapsulated inside their respective fetchers.
    """
    import inspect
    from data_providers.fyers_fetcher import FyersFetcher
    from data_providers.unified_fetcher import UnifiedFetcher
    
    fyers_source = inspect.getsource(FyersFetcher)
    unified_source = inspect.getsource(UnifiedFetcher)
    
    # 1. FyersFetcher must isolate symbol normalization, candidate suffixes, and index history protection internally
    assert '_normalize_symbol' in fyers_source, "FyersFetcher must encapsulate _normalize_symbol internally"
    assert '_generate_fyers_candidate_symbols' in fyers_source, "FyersFetcher must encapsulate candidate resolution internally"
    assert '-INDEX' in fyers_source, "FyersFetcher must handle index symbols internally"
    
    # 2. UnifiedFetcher must isolate multi-exchange quote chunking (NSE:, BSE:, MCX:) internally
    assert 'norm.startswith("BSE:")' in unified_source, "UnifiedFetcher must handle BSE: symbol quotes internally"
    assert 'norm.startswith("NSE:")' in unified_source, "UnifiedFetcher must handle NSE: symbol quotes internally"

