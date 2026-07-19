import logging
import json
from abc import ABC, abstractmethod
from typing import Optional, List
from .result import ValidatedDataset, ValidationStatus
from .registry import DatasetType
from database import get_connection

logger = logging.getLogger(__name__)

class ValidationHistoryRecorder(ABC):
    """
    Interface for recording validation history.
    Decouples the validation framework from persistence details.
    """
    @abstractmethod
    def record_single(self, dataset_type: DatasetType, validated_dataset: ValidatedDataset) -> None:
        """Records a single dataset validation event (e.g., Bhavcopy, Symbol Master)."""
        pass
        
    @abstractmethod
    def record_batch(self, dataset_type: DatasetType, results: List[ValidatedDataset], fallback_status: Optional[ValidationStatus] = None) -> None:
        """Records a batch of validations as a single summary event (e.g., PRICE batch)."""
        pass


class PostgresValidationHistoryRecorder(ValidationHistoryRecorder):
    def record_single(self, dataset_type: DatasetType, validated_dataset: ValidatedDataset) -> None:
        try:
            report = validated_dataset.result
            score = validated_dataset.score
            status = validated_dataset.status.name
            
            failures_json = json.dumps([f.message for f in report.critical_failures]) if report.critical_failures else None
            warnings_json = json.dumps(report.warnings) if report.warnings else None
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute('''
                        INSERT INTO validation_history (
                            dataset_name, score, status, failures, warnings, 
                            row_count, validator_version,
                            symbols_processed, symbols_valid, symbols_failed,
                            average_score, minimum_score, maximum_score
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                    ''', (
                        dataset_type.name, score, status, failures_json, warnings_json,
                        getattr(report.metrics, 'row_count', 0) if hasattr(report, 'metrics') else getattr(report, 'row_count', 0),
                        "1.0", # Can pull from engine if passed, defaulting to 1.0
                        1, 1 if status != ValidationStatus.INVALID.name else 0, 1 if status == ValidationStatus.INVALID.name else 0,
                        score, score, score
                    ))
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to record validation history for {dataset_type.name}: {e}")

    def record_batch(self, dataset_type: DatasetType, results: List[ValidatedDataset], fallback_status: Optional[ValidationStatus] = None) -> None:
        if not results:
            return
            
        try:
            processed = len(results)
            valid_results = [r for r in results if r.status != ValidationStatus.INVALID]
            failed_results = [r for r in results if r.status == ValidationStatus.INVALID]
            
            valid_count = len(valid_results)
            failed_count = len(failed_results)
            
            scores = [r.score for r in valid_results]
            
            avg_score = sum(scores) / len(scores) if scores else 0.0
            min_score = min(scores) if scores else 0.0
            max_score = max(scores) if scores else 0.0
            
            if failed_count > 0:
                status = ValidationStatus.INVALID.name
            elif fallback_status:
                status = fallback_status.name
            elif avg_score < 90.0 or any(r.status == ValidationStatus.DEGRADED for r in valid_results):
                status = ValidationStatus.DEGRADED.name
            else:
                status = ValidationStatus.VALID.name
                
            # Aggregate warnings across the batch (limit to 10 distinct to avoid overflow)
            all_warnings = set()
            for r in valid_results:
                if r.result and r.result.warnings:
                    all_warnings.update(r.result.warnings)
            warnings_list = list(all_warnings)[:10]
            warnings_json = json.dumps(warnings_list) if warnings_list else None
            
            # Aggregate failures
            all_failures = set()
            for r in failed_results:
                if r.result and r.result.critical_failures:
                    for f in r.result.critical_failures:
                        all_failures.add(f.message)
            failures_list = list(all_failures)[:10]
            failures_json = json.dumps(failures_list) if failures_list else None
            
            total_rows = sum(
                getattr(r.result.metrics, 'row_count', 0) if hasattr(r.result, 'metrics') else getattr(r.result, 'row_count', 0)
                for r in valid_results if r.result
            )
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute('''
                        INSERT INTO validation_history (
                            dataset_name, score, status, failures, warnings, 
                            row_count, validator_version,
                            symbols_processed, symbols_valid, symbols_failed,
                            average_score, minimum_score, maximum_score
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                    ''', (
                        dataset_type.name, avg_score, status, failures_json, warnings_json,
                        total_rows, "1.0",
                        processed, valid_count, failed_count,
                        avg_score, min_score, max_score
                    ))
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to record batch validation history for {dataset_type.name}: {e}")

# Global instance
history_recorder = PostgresValidationHistoryRecorder()
