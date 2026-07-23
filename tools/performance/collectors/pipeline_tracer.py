import time
import json
from collections import defaultdict

class PipelineTracer:
    """
    Tracks execution time across the entire scanner pipeline stages.
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PipelineTracer, cls).__new__(cls)
            cls._instance.metrics = defaultdict(float)
            cls._instance.counts = defaultdict(int)
        return cls._instance

    class TraceStage:
        def __init__(self, stage_name: str):
            self.stage_name = stage_name
            self.start_time = 0
            
        def __enter__(self):
            self.start_time = time.monotonic()
            return self
            
        def __exit__(self, exc_type, exc_val, exc_tb):
            elapsed = time.monotonic() - self.start_time
            tracer = PipelineTracer()
            tracer.metrics[self.stage_name] += elapsed
            tracer.counts[self.stage_name] += 1

    @classmethod
    def trace(cls, stage_name: str):
        return cls.TraceStage(stage_name)
        
    @classmethod
    def get_metrics(cls) -> dict:
        tracer = cls()
        return {
            "latencies": dict(tracer.metrics),
            "counts": dict(tracer.counts)
        }
        
    @classmethod
    def clear(cls):
        tracer = cls()
        tracer.metrics.clear()
        tracer.counts.clear()
        
    @classmethod
    def dump_metrics(cls, filepath: str):
        with open(filepath, 'w') as f:
            json.dump(cls.get_metrics(), f, indent=4)
