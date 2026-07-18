from enum import Enum, auto

class ProviderResult(Enum):
    SUCCESS = auto()
    NOT_FOUND = auto()
    RATE_LIMIT = auto()
    NETWORK_ERROR = auto()
    TIMEOUT = auto()
    EMPTY_DATA = auto()
    MARKET_CLOSED = auto()

class MappingState(Enum):
    ACTIVE = auto()
    INVALID = auto()
    TEMP_DISABLED = auto()

class ScannerOutcome(Enum):
    RUNNING = auto()
    SUCCESS = auto()
    PARTIAL_SUCCESS = auto()
    FAILED = auto()
    ABORTED = auto()
