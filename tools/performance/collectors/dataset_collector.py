import time
import json
from collections import defaultdict

class DatasetCollector:
    """
    Collects DatasetRegistry hit rates, latency, and refresh timing.
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DatasetCollector, cls).__new__(cls)
            cls._instance.metrics = defaultdict(lambda: {
                "hits": 0,
                "misses": 0,
                "latency_sec": 0.0,
                "refresh_count": 0,
                "refresh_latency_sec": 0.0
            })
        return cls._instance

    @classmethod
    def record_hit(cls, dataset_name: str, latency: float):
        col = cls()
        col.metrics[dataset_name]["hits"] += 1
        col.metrics[dataset_name]["latency_sec"] += latency

    @classmethod
    def record_miss(cls, dataset_name: str, latency: float):
        col = cls()
        col.metrics[dataset_name]["misses"] += 1
        col.metrics[dataset_name]["latency_sec"] += latency

    @classmethod
    def record_refresh(cls, dataset_name: str, latency: float):
        col = cls()
        col.metrics[dataset_name]["refresh_count"] += 1
        col.metrics[dataset_name]["refresh_latency_sec"] += latency

    @classmethod
    def get_metrics(cls) -> dict:
        return dict(cls().metrics)

    @classmethod
    def dump_metrics(cls, filepath: str):
        with open(filepath, 'w') as f:
            json.dump(cls.get_metrics(), f, indent=4)
