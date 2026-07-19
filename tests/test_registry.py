import pytest
from app.validation.registry import ValidationRegistry, DatasetType
from app.validation.base import BaseValidator
from app.validation.scoring.base import BaseScoreCalculator
from app.validation.validators.price_validator import PriceValidator
from app.validation.scoring.price_score import PriceScoreCalculator

def test_registry_enforces_coverage():
    """
    Architectural rule test:
    Validates that the ValidationRegistry contains an entry for EVERY DatasetType.
    This prevents developers from adding a DatasetType without wiring the framework.
    """
    registry = ValidationRegistry()
    
    # We expect all DatasetTypes to either have a valid pipeline or explicitly raise NotImplementedError
    for ds_type in DatasetType:
        try:
            pipeline = registry.get_pipeline(ds_type)
            # If a pipeline is returned, it must conform to the base interfaces
            assert isinstance(pipeline.validator, BaseValidator)
            if pipeline.score_calculator is not None:
                assert isinstance(pipeline.score_calculator, BaseScoreCalculator)
        except NotImplementedError:
            # It's acceptable for a pipeline to not be implemented yet during rollout.
            # This proves the registry is tracking the dataset.
            pass

def test_registry_returns_price_validator():
    """
    Test that the core Tier 1 Price validation pipeline is fully assembled and returned.
    """
    registry = ValidationRegistry()
    pipeline = registry.get_pipeline(DatasetType.PRICE)
    
    assert pipeline is not None
    assert isinstance(pipeline.validator, PriceValidator)
    assert isinstance(pipeline.score_calculator, PriceScoreCalculator)

def test_registry_is_singleton():
    """
    Ensures that eager initialization happens only once.
    """
    r1 = ValidationRegistry()
    r2 = ValidationRegistry()
    assert r1 is r2
