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
