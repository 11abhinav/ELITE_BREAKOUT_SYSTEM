"""
app/data_providers/data_quarantine_manager.py
==============================================
Production Data Quarantine and Failure Lifecycle State Machine.

Lifecycle Invariant:
1. API_DATA_GENUINELY_UNAVAILABLE:
   - 7 calendar day cooldown quarantine.
   - Prevents hammering upstream APIs while data is proven absent.
   - Revalidation executes the full provenance chain upon expiration.

2. STRUCTURAL_HISTORY_DEFICIT (e.g. VINYAS, HARIOMPIPE):
   - Company listing age < 5 years; exchange API only covers post-listing filings.
   - Low-frequency / event-driven annual filing wait (90-day cooldown).
   - Zero redundant daily/weekly network hammering.

3. CORPORATE_RESTRUCTURING_REMAPPING (e.g. GUJGASLTD):
   - Old ticker / entity merged or renamed on the exchange.
   - 0-day cooldown: Trigger immediate security identity and instrument remapping.

4. API_HAS_DATA_PARSER_FAILURE / CALCULATION_FAILURE:
   - 0-day cooldown: Fix parser / cache and immediately reprocess.

5. API_VALUE_CONFIRMED_BUT_INVALID_FOR_RULE (e.g. negative base PAT turnaround):
   - Permanent block from this specific strategy rule (zero re-fetch needed).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
from zoneinfo import ZoneInfo

try:
    from app.data_providers.fundamental_models import (
        DataFailureClass,
        QuarantineAction,
        DataQuarantineRecord,
    )
except ImportError:
    from data_providers.fundamental_models import (
        DataFailureClass,
        QuarantineAction,
        DataQuarantineRecord,
    )

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QUARANTINE_FILE = os.path.join(BASE_DIR, "data", "data_unavailable_quarantine.json")


class DataQuarantineManager:
    """Manages data quarantine cooldowns and automated revalidation policy."""

    def __init__(self, quarantine_file: str = QUARANTINE_FILE):
        self.quarantine_file = quarantine_file
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.quarantine_file):
            try:
                with open(self.quarantine_file, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
            except Exception as e:
                logger.warning(f"[QUARANTINE] Failed to load {self.quarantine_file}: {e}")
                self._cache = {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.quarantine_file), exist_ok=True)
        tmp = self.quarantine_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2, sort_keys=True)
            os.replace(tmp, self.quarantine_file)
        except Exception as e:
            logger.error(f"[QUARANTINE] Failed to save {self.quarantine_file}: {e}")

    def evaluate_quarantine_policy(
        self,
        symbol: str,
        metric: str,
        failure_class: DataFailureClass,
        details: Optional[Dict[str, Any]] = None,
    ) -> Tuple[QuarantineAction, int]:
        """
        Determines the exact operational action and cooldown duration based on
        failure root cause.
        """
        details = details or {}

        # 1. Corporate Restructuring / Renaming: Immediate remapping
        if failure_class == DataFailureClass.CORPORATE_RESTRUCTURING_REMAPPING:
            return QuarantineAction.IMMEDIATE_REMAPPING, 0

        # 2. Parser Failure: Immediate code fix / reprocess
        if failure_class == DataFailureClass.API_HAS_DATA_PARSER_FAILURE:
            return QuarantineAction.IMMEDIATE_REPROCESS, 0

        # 3. Calculation / Cache Gap: Immediate calculation / cache rebuild
        if failure_class == DataFailureClass.CALCULATION_FAILURE:
            return QuarantineAction.IMMEDIATE_REPROCESS, 0

        # 4. Turnaround / Mathematical Rule Invalidation: Block from strategy, no re-fetch
        if failure_class == DataFailureClass.API_VALUE_CONFIRMED_BUT_INVALID_FOR_RULE:
            return QuarantineAction.STRATEGY_BLOCK_NO_REFETCH, 365

        # 5. Structural History Deficit (< 5 years listing age): Event-driven annual wait
        if failure_class == DataFailureClass.STRUCTURAL_HISTORY_DEFICIT:
            return QuarantineAction.EVENT_DRIVEN_ANNUAL_WAIT, 90

        # 6. Period Gap: 7 calendar days cooldown
        if failure_class == DataFailureClass.API_HAS_DATA_BUT_PERIOD_GAP:
            return QuarantineAction.QUARANTINE_COOLDOWN, 7

        # 7. Scope Mismatch: 14 calendar days cooldown
        if failure_class == DataFailureClass.API_HAS_DATA_BUT_SCOPE_MISMATCH:
            return QuarantineAction.QUARANTINE_COOLDOWN, 14

        # 8. Genuine Data Absence at exchange API level: 7 calendar days cooldown
        return QuarantineAction.QUARANTINE_COOLDOWN, 7

    def register_failure(
        self,
        symbol: str,
        metric: str,
        failure_class: DataFailureClass,
        provenance_hash: str,
        reason: str,
        details: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> DataQuarantineRecord:
        """Registers a verified failure into quarantine state."""
        current_time = now or datetime.now(IST)
        action, cooldown_days = self.evaluate_quarantine_policy(
            symbol=symbol,
            metric=metric,
            failure_class=failure_class,
            details=details,
        )

        cooldown_until = current_time + timedelta(days=cooldown_days)
        record = DataQuarantineRecord(
            symbol=symbol,
            metric=metric,
            failure_class=failure_class.value,
            action=action.value,
            quarantined_at=current_time.isoformat(),
            cooldown_until=cooldown_until.isoformat(),
            cooldown_days=cooldown_days,
            provenance_hash=provenance_hash or "NONE",
            reason=reason,
        )

        key = f"{symbol}::{metric}"
        self._cache[key] = {
            "symbol": record.symbol,
            "metric": record.metric,
            "failure_class": record.failure_class,
            "action": record.action,
            "quarantined_at": record.quarantined_at,
            "cooldown_until": record.cooldown_until,
            "cooldown_days": record.cooldown_days,
            "provenance_hash": record.provenance_hash,
            "reason": record.reason,
        }
        self._save()

        logger.info(
            f"🛡️ [QUARANTINE: REGISTERED] {symbol} ({metric}): action={action.value}, "
            f"cooldown={cooldown_days}d until {record.cooldown_until} | reason={reason}"
        )
        return record

    def is_quarantine_active(
        self,
        symbol: str,
        metric: str,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[DataQuarantineRecord]]:
        """
        Returns (True, record) if the symbol is in active quarantine cooldown.
        Scanner must SKIP network calls when active, avoiding redundant provider hammering.
        """
        key = f"{symbol}::{metric}"
        item = self._cache.get(key)
        if not item:
            # Check wildcard metric
            item = self._cache.get(f"{symbol}::ALL")
            if not item:
                return False, None

        current_time = now or datetime.now(IST)
        try:
            until_dt = datetime.fromisoformat(item["cooldown_until"])
            if until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=IST)
            if current_time < until_dt:
                rec = DataQuarantineRecord(
                    symbol=item["symbol"],
                    metric=item["metric"],
                    failure_class=item["failure_class"],
                    action=item["action"],
                    quarantined_at=item["quarantined_at"],
                    cooldown_until=item["cooldown_until"],
                    cooldown_days=item["cooldown_days"],
                    provenance_hash=item["provenance_hash"],
                    reason=item["reason"],
                )
                return True, rec
        except Exception as e:
            logger.debug(f"[QUARANTINE] Date parse error for {key}: {e}")

        return False, None

    def clear_quarantine(self, symbol: str, metric: Optional[str] = None) -> None:
        """Clears quarantine when revalidation passes."""
        if metric:
            key = f"{symbol}::{metric}"
            if key in self._cache:
                del self._cache[key]
                self._save()
        else:
            to_del = [k for k in self._cache if k.startswith(f"{symbol}::")]
            for k in to_del:
                del self._cache[k]
            if to_del:
                self._save()
