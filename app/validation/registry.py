from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional, Dict

from .base import BaseValidator
from .scoring.base import BaseScoreCalculator
from .validators.price_validator import PriceValidator
from .scoring.price_score import PriceScoreCalculator

class DatasetType(Enum):
    """Enumeration of all supported external data types in the system."""
    PRICE = auto()
    BHAVCOPY = auto()
    DELIVERY = auto()
    LIVE_QUOTES = auto()
    FUNDAMENTALS = auto()
    CORPORATE_ACTIONS = auto()
    SYMBOL_MASTER = auto()
    INDEX_CONSTITUENTS = auto()
    MARKET_BREADTH = auto()
    HOLIDAYS = auto()

@dataclass(frozen=True)
class ValidationPipeline:
    """Contains the validator and score calculator associated with a dataset type."""
    validator: BaseValidator
    score_calculator: Optional[BaseScoreCalculator]

class ValidationRegistry:
    """
    Central authoritative registry for all supported dataset validations.
    Enforces that every dataset type has an associated validator pipeline.
    Eagerly loads all validators on initialization to fail fast.
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ValidationRegistry, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self._pipelines: Dict[DatasetType, ValidationPipeline] = {}
        
        # Tier 1 (Core Trading)
        self._register(DatasetType.PRICE, PriceValidator(), PriceScoreCalculator())
        
        # To be implemented:
        self._register_stub(DatasetType.BHAVCOPY)
        self._register_stub(DatasetType.DELIVERY)
        self._register_stub(DatasetType.SYMBOL_MASTER)
        
        # Tier 2
        self._register_stub(DatasetType.FUNDAMENTALS)
        self._register_stub(DatasetType.CORPORATE_ACTIONS)
        self._register_stub(DatasetType.INDEX_CONSTITUENTS)
        
        # Tier 3
        self._register_stub(DatasetType.LIVE_QUOTES)
        self._register_stub(DatasetType.MARKET_BREADTH)
        
        # Tier 4
        self._register_stub(DatasetType.HOLIDAYS)

    def _register(self, dataset: DatasetType, validator: BaseValidator, score_calculator: Optional[BaseScoreCalculator] = None):
        self._pipelines[dataset] = ValidationPipeline(validator, score_calculator)
        
    def _register_stub(self, dataset: DatasetType):
        # Temporarily register None until the validator is implemented.
        # test_registry.py will assert these are populated when strictly enforcing framework rules.
        self._pipelines[dataset] = None

    def get_pipeline(self, dataset: DatasetType) -> ValidationPipeline:
        pipeline = self._pipelines.get(dataset)
        if pipeline is None:
            raise NotImplementedError(f"Validator pipeline for {dataset.name} is not yet implemented.")
        return pipeline

registry = ValidationRegistry()
