from dataclasses import dataclass, field

@dataclass
class ScanFailure:
    symbol: str
    provider: str
    reason: str
    exception_type: str
    scanner: str
    stage: str

@dataclass
class ProviderStats:
    provider_name: str
    requests: int = 0
    retries: int = 0
    fallbacks: int = 0
    failures: int = 0

    def record_request(self):
        self.requests += 1

    def record_retry(self):
        self.retries += 1
        
    def record_fallback(self):
        self.fallbacks += 1
        
    def record_failure(self):
        self.failures += 1

@dataclass
class ScannerMetrics:
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    provider_stats: dict[str, ProviderStats] = field(default_factory=dict)

    def get_provider(self, name: str) -> ProviderStats:
        if name not in self.provider_stats:
            self.provider_stats[name] = ProviderStats(provider_name=name)
        return self.provider_stats[name]
