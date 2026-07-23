import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Set

class StorageTier(str, Enum):
    EPHEMERAL = "EPHEMERAL"  # Rebuildable, lost on restart (memory, parquet, json)
    DURABLE = "DURABLE"      # Persists across restarts (postgres)

@dataclass
class DatasetEntry:
    id: str
    owner: str
    tier: StorageTier
    cadence: int               # Seconds between refreshes
    consumers: Set[str] = field(default_factory=set)
    depends_on: List[str] = field(default_factory=list)
    release_event: Optional[str] = None
    preferred_provider: Optional[str] = None
    provider_used: Optional[str] = None
    
    # Metadata for legacy compatibility
    period: Optional[str] = None
    resolution: Optional[str] = None
    min_rows: Optional[int] = None
    
    # Runtime state
    generation: int = 0
    last_refresh: float = 0.0

class DatasetRegistry:
    """
    Authoritative source for all shared datasets, following ARCHITECTURE_FREEZE.md constraints.
    No duplicate sources of truth.
    """
    def __init__(self):
        self._datasets: Dict[str, DatasetEntry] = {}
        self._data: Dict[str, Any] = {}
        self._init_legacy_datasets()
        
    def _init_legacy_datasets(self):
        # Existing datasets with explicit StorageTier.EPHEMERAL
        # Per §1 of ARCHITECTURE_FREEZE.md: price_1m/intraday -> fyers, historical -> yahoo
        self.register_dataset(DatasetEntry(id="price_1m", owner="HistoricalDataManager", tier=StorageTier.EPHEMERAL, cadence=60, period="1mo", resolution="1m", min_rows=50, preferred_provider="fyers"))
        self.register_dataset(DatasetEntry(id="price_15m", owner="HistoricalDataManager", tier=StorageTier.EPHEMERAL, cadence=900, period="6mo", resolution="15m", preferred_provider="fyers"))
        self.register_dataset(DatasetEntry(id="price_1d", owner="HistoricalDataManager", tier=StorageTier.EPHEMERAL, cadence=86400, period="1y", resolution="1d", preferred_provider="yahoo"))
        self.register_dataset(DatasetEntry(id="promoter_pledge", owner="HistoricalDataManager", tier=StorageTier.EPHEMERAL, cadence=86400, preferred_provider="nse"))
        self.register_dataset(DatasetEntry(id="fundamentals_quarterly", owner="HistoricalDataManager", tier=StorageTier.EPHEMERAL, cadence=90*86400, preferred_provider="yahoo"))
        self.register_dataset(DatasetEntry(id="company_profile", owner="HistoricalDataManager", tier=StorageTier.EPHEMERAL, cadence=30*86400, preferred_provider="yahoo"))
        
        # Fundamentals governed dataset
        self.register_dataset(DatasetEntry(id="fundamentals_cache", owner="FundamentalsManager", tier=StorageTier.DURABLE, cadence=86400, preferred_provider="yahoo"))
        
        # Freeze reconciliation additions (Phase 2 & Completion Sprint)
        self.register_dataset(DatasetEntry(id="block_deals", owner="InstitutionalDataManager", tier=StorageTier.EPHEMERAL, cadence=86400, preferred_provider="nse"))
        self.register_dataset(DatasetEntry(id="bhavcopy_delivery", owner="DeliveryDataManager", tier=StorageTier.EPHEMERAL, cadence=86400, preferred_provider="nse"))
        self.register_dataset(DatasetEntry(id="blacklist", owner="SurveillanceManager", tier=StorageTier.EPHEMERAL, cadence=3600, preferred_provider="nse"))
        
        # Migrated caches
        self.register_dataset(DatasetEntry(id="watchlist", owner="DailyBuilder", tier=StorageTier.EPHEMERAL, cadence=86400))
        self.register_dataset(DatasetEntry(id="indices_cache", owner="DashboardServer", tier=StorageTier.EPHEMERAL, cadence=900, preferred_provider="yahoo"))
        self.register_dataset(DatasetEntry(id="wealth_cache", owner="DashboardServer", tier=StorageTier.EPHEMERAL, cadence=900))
        self.register_dataset(DatasetEntry(id="sector_rotation", owner="SectorRotationEngine", tier=StorageTier.EPHEMERAL, cadence=1800, preferred_provider="yahoo"))

    def register_dataset(self, entry: DatasetEntry) -> None:
        self._datasets[entry.id] = entry

    def register_consumer(self, dataset_id: str, consumer_name: str) -> None:
        if dataset_id not in self._datasets:
            raise ValueError(f"Dataset {dataset_id} not registered in DatasetRegistry.")
        self._datasets[dataset_id].consumers.add(consumer_name)

    def get(self, dataset_id: str) -> Any:
        return self._data.get(dataset_id)

    def put(self, dataset_id: str, data: Any) -> None:
        if dataset_id not in self._datasets:
            raise ValueError(f"Dataset {dataset_id} not registered in DatasetRegistry.")
        self._data[dataset_id] = data
        self._datasets[dataset_id].generation += 1
        self._datasets[dataset_id].last_refresh = time.time()
        
    def get_entry(self, dataset_id: str) -> Optional[DatasetEntry]:
        return self._datasets.get(dataset_id)

    def validate(self) -> None:
        """
        Run at startup to validate the registry graph.
        """
        errors = []
        for d_id, entry in self._datasets.items():
            for dep in entry.depends_on:
                if dep not in self._datasets:
                    errors.append(f"Dataset '{d_id}' depends on unregistered dataset '{dep}'.")
        
        if errors:
            raise RuntimeError("Registry validation failed:\n" + "\n".join(errors))

# Global singleton registry instance
registry = DatasetRegistry()
