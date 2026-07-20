from abc import ABC, abstractmethod
import logging
from core.invariants import InvariantResult, Severity, InvariantViolation

logger = logging.getLogger(__name__)

class ExecutionPolicy(ABC):
    """Defines how invariant results are handled (raised, logged, or ignored)."""
    
    @abstractmethod
    def handle_result(self, result: InvariantResult) -> None:
        pass

class ProductionPolicy(ExecutionPolicy):
    """
    Standard production behavior.
    - CRITICAL: Raise InvariantViolation to halt the pipeline for this symbol.
    - WARNING/INFO: Log heavily for analytics and governance, but allow processing to continue.
    """
    def handle_result(self, result: InvariantResult) -> None:
        if result.status == "FAIL":
            if result.severity == Severity.CRITICAL:
                logger.error(f"CRITICAL INVARIANT VIOLATION: {result.invariant_id} - {result.message}")
                raise InvariantViolation(result.invariant_id, result.severity, result.symbol, result.message)
            elif result.severity == Severity.WARNING:
                logger.warning(f"WARNING INVARIANT VIOLATION: {result.invariant_id} - {result.message}")
            elif result.severity == Severity.INFO:
                logger.info(f"INFO INVARIANT VIOLATION: {result.invariant_id} - {result.message}")
        else:
            # Optionally log passing INFO invariants or all for deep tracing
            pass

class StrictTestPolicy(ExecutionPolicy):
    """
    Used for generating baseline datasets and CI verification.
    - CRITICAL and WARNING: Raise InvariantViolation to ensure pristine baseline behaviors.
    - INFO: Log for observation.
    """
    def handle_result(self, result: InvariantResult) -> None:
        if result.status == "FAIL":
            if result.severity in (Severity.CRITICAL, Severity.WARNING):
                logger.error(f"STRICT TEST VIOLATION: {result.invariant_id} ({result.severity.value}) - {result.message}")
                raise InvariantViolation(result.invariant_id, result.severity, result.symbol, result.message)
            elif result.severity == Severity.INFO:
                logger.info(f"INFO INVARIANT VIOLATION: {result.invariant_id} - {result.message}")
