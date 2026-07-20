import os
import json
from typing import Any, Dict
from datetime import datetime
from core.events import (
    EventSubscriber, DomainEvent,
    ValidationCompleted, IndicatorsCalculated,
    ScannerCompleted, ScoresCalculated,
    CandidateSelected, SLTargetComputed, AlertCreated
)

def _normalize_dict(d: Any) -> Any:
    """Recursively normalizes floats for stable JSON serialization and sorts keys."""
    if not isinstance(d, dict):
        if isinstance(d, float):
            return round(d, 4)
        if hasattr(d, "isoformat"):
            return d.isoformat()
        return d
    
    out = {}
    for k in sorted(d.keys()):
        v = d[k]
        if isinstance(v, float):
            if any(term in k.lower() for term in ["score", "margin", "ratio", "pct", "percent"]):
                out[k] = round(v, 2)
            else:
                out[k] = round(v, 4)
        elif isinstance(v, dict):
            out[k] = _normalize_dict(v)
        elif isinstance(v, list):
            out[k] = [_normalize_dict(i) for i in v]
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out

class GoldenSnapshotCollector(EventSubscriber):
    def __init__(self, dataset_name: str, output_dir: str):
        self.dataset_name = dataset_name
        self.output_dir = output_dir
        self.pipeline_version = "Constitution v1.0"
        self.schema_version = "1.0"
        self.captured_stages = []
        
        self.stage_map = {
            ValidationCompleted: "01_validation",
            IndicatorsCalculated: "02_indicators",
            ScannerCompleted: "03_scanner",
            ScoresCalculated: "04_scores",
            CandidateSelected: "05_candidate",
            SLTargetComputed: "06_sl_target",
            AlertCreated: "07_alert",
        }
        
        self.expected_sequence = [
            "01_validation", "02_indicators", "03_scanner", 
            "04_scores", "05_candidate", "06_sl_target", "07_alert"
        ]
        
    def on_event(self, event: DomainEvent) -> None:
        stage_name = self.stage_map.get(type(event))
        if not stage_name:
            return
            
        # Event Ordering check
        expected_next = self.expected_sequence[len(self.captured_stages)] if len(self.captured_stages) < len(self.expected_sequence) else None
        if stage_name != expected_next:
            raise RuntimeError(f"Event ordering violation for {self.dataset_name}: Expected {expected_next}, but got {stage_name}")
            
        self._write_snapshot(stage_name, event.payload)
        self.captured_stages.append(stage_name)
        
    def assert_coverage(self):
        """Asserts that all 7 stages fired before pipeline exit."""
        if len(self.captured_stages) < len(self.expected_sequence):
            missing = set(self.expected_sequence) - set(self.captured_stages)
            raise RuntimeError(f"Snapshot coverage violation for {self.dataset_name}. Missing stages: {missing}")
            
    def _write_snapshot(self, stage_name: str, payload: Any):
        os.makedirs(self.output_dir, exist_ok=True)
        filepath = os.path.join(self.output_dir, f"{stage_name}.json")
        
        normalized_data = _normalize_dict(payload)
        
        snapshot = {
            "_metadata": {
                "snapshot_version": "1.0",
                "dataset": self.dataset_name,
                "stage": stage_name,
                "schema_version": self.schema_version,
                "pipeline_version": self.pipeline_version
            },
            "data": normalized_data
        }
        
        with open(filepath, "w") as f:
            json.dump(snapshot, f, indent=2, sort_keys=True)
