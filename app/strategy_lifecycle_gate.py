"""
PERSISTENT STRATEGY LIFECYCLE GATE (Q31-Q34)
Cross-process, persistent, transactionally protected lifecycle synchronization gate enforcing (strategy_id, symbol) identity.

Architectural Contract:
1. Canonical Key Identity: (strategy_id, symbol) - Never symbol alone.
2. Single Persistent Source of Truth: Backed by SQLite database table `strategy_lifecycles` in `buy_alerts_journal.db`.
3. Same-Strategy BUY/EXIT Synchronization (Q31 & Q33):
   - OPEN / ACTIVE             -> BLOCK BUY
   - EXIT_PENDING              -> BLOCK BUY
   - SELL_REVIEW               -> BLOCK BUY
   - SELL                      -> BLOCK BUY
   - CLOSED (same trading day) -> BLOCK BUY (same-day re-entry policy)
   - CLOSED (prior days + stale signal) -> BLOCK BUY (Q33b stale signal guard)
   - CLOSED (prior days + new genuine signal) -> ALLOW BUY
4. Cross-Strategy Non-Blocking (Q32):
   - QUALITY_COMPOUNDER + RAILTEL (OPEN / EXIT) DOES NOT block QUALITY_VALUE_RECOVERY + RAILTEL (BUY).
5. Cross-Process & Restart Race Safety (Q34a-Q34d):
   - SQLite transactional `BEGIN IMMEDIATE` lock + UNIQUE constraint on (strategy_id, symbol) for active records.
   - Process-restart safe (persists across process deaths and restarts).
   - Shared BUY and EXIT atomic transaction boundary.
"""

import os
import sqlite3
import threading
from typing import Dict, Tuple, Optional, Any, List
from datetime import datetime, date


class StrategyLifecycleState:
    OPEN = "OPEN"
    EXIT_PENDING = "EXIT_PENDING"
    SELL_REVIEW = "SELL_REVIEW"
    SELL = "SELL"
    CLOSED = "CLOSED"


class PersistentStrategyLifecycleGate:
    """
    Authoritative state synchronization gate for strategy lifecycles.
    Cross-process thread-safe and transactionally backed by SQLite persistent DB.
    """

    def __init__(self, db_path: Optional[str] = None):
        self._lock = threading.RLock()
        if db_path is None:
            base_dir = os.getenv("ELITE_BASE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            db_path = os.path.join(base_dir, "data", "buy_alerts_journal.db")
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS strategy_lifecycles (
                        strategy_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        status TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        signal_hash TEXT,
                        entry_date TEXT NOT NULL,
                        closed_date TEXT,
                        exit_reason_code TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (strategy_id, symbol)
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_st_lifecycles_status ON strategy_lifecycles(strategy_id, symbol, status);")
                conn.commit()

    def _get_key(self, strategy_id: str, symbol: str) -> Tuple[str, str]:
        return (str(strategy_id).strip().upper(), str(symbol).strip().upper())

    def get_lifecycle_status(self, strategy_id: str, symbol: str) -> Optional[Dict[str, Any]]:
        key = self._get_key(strategy_id, symbol)
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT * FROM strategy_lifecycles WHERE strategy_id = ? AND symbol = ?",
                    (key[0], key[1])
                )
                row = cur.fetchone()
                if row:
                    return dict(row)
                return None

    def evaluate_buy_eligibility(
        self,
        strategy_id: str,
        symbol: str,
        current_date: Optional[date] = None,
        signal_hash: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Evaluates whether a BUY alert can be committed for (strategy_id, symbol).
        Returns (is_eligible, reason_code).
        """
        key = self._get_key(strategy_id, symbol)
        req_date = (current_date or datetime.now().date()).strftime("%Y-%m-%d")

        with self._lock:
            lc = self.get_lifecycle_status(strategy_id, symbol)
            if not lc:
                return (True, "NO_ACTIVE_LIFECYCLE")

            status = lc.get("status")
            closed_date = lc.get("closed_date")

            # 1. Active / Pending / Review States -> HARD BLOCK
            if status in (StrategyLifecycleState.OPEN, "ACTIVE"):
                return (False, f"SAME_STRATEGY_POSITION_OPEN ({key[0]}:{key[1]})")

            if status == StrategyLifecycleState.EXIT_PENDING:
                return (False, f"SAME_STRATEGY_EXIT_PENDING ({key[0]}:{key[1]})")

            if status == StrategyLifecycleState.SELL_REVIEW:
                return (False, f"SAME_STRATEGY_SELL_REVIEW_ACTIVE ({key[0]}:{key[1]})")

            if status == StrategyLifecycleState.SELL:
                return (False, f"SAME_STRATEGY_SELL_EXECUTING ({key[0]}:{key[1]})")

            # 2. Closed Position Re-entry Policy
            if status == StrategyLifecycleState.CLOSED:
                if closed_date and closed_date == req_date:
                    return (False, f"SAME_STRATEGY_SAME_DAY_REENTRY_BLOCKED ({key[0]}:{key[1]})")

                # Q33b Stale Signal Guard: Compare signal_hash if provided
                prev_signal_hash = lc.get("signal_hash")
                if signal_hash and prev_signal_hash and str(signal_hash).strip() == str(prev_signal_hash).strip():
                    return (False, f"SAME_STRATEGY_STALE_SIGNAL_REENTRY_BLOCKED ({key[0]}:{key[1]})")

                return (True, "SAME_STRATEGY_PRIOR_CLOSE_EXPIRED")

            return (True, "LIFECYCLE_CLEARED")

    def register_buy_transactional(
        self,
        strategy_id: str,
        symbol: str,
        entry_price: float,
        current_date: Optional[date] = None,
        signal_hash: Optional[str] = None
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Cross-Process Atomic Transactional Check + Insert/Update operation (Q34 Race Safety).
        Uses SQLite BEGIN IMMEDIATE to acquire a write lock across all process workers.
        """
        key = self._get_key(strategy_id, symbol)
        req_date = (current_date or datetime.now().date()).strftime("%Y-%m-%d")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                cur = conn.cursor()
                cur.execute(
                    "SELECT status, closed_date, signal_hash FROM strategy_lifecycles WHERE strategy_id = ? AND symbol = ?",
                    (key[0], key[1])
                )
                row = cur.fetchone()

                if row:
                    status = row["status"]
                    closed_date = row["closed_date"]
                    prev_signal_hash = row["signal_hash"]

                    if status in (StrategyLifecycleState.OPEN, "ACTIVE"):
                        conn.rollback()
                        return (False, f"SAME_STRATEGY_POSITION_OPEN ({key[0]}:{key[1]})", None)

                    if status == StrategyLifecycleState.EXIT_PENDING:
                        conn.rollback()
                        return (False, f"SAME_STRATEGY_EXIT_PENDING ({key[0]}:{key[1]})", None)

                    if status == StrategyLifecycleState.SELL_REVIEW:
                        conn.rollback()
                        return (False, f"SAME_STRATEGY_SELL_REVIEW_ACTIVE ({key[0]}:{key[1]})", None)

                    if status == StrategyLifecycleState.SELL:
                        conn.rollback()
                        return (False, f"SAME_STRATEGY_SELL_EXECUTING ({key[0]}:{key[1]})", None)

                    if status == StrategyLifecycleState.CLOSED:
                        if closed_date and closed_date == req_date:
                            conn.rollback()
                            return (False, f"SAME_STRATEGY_SAME_DAY_REENTRY_BLOCKED ({key[0]}:{key[1]})", None)

                        if signal_hash and prev_signal_hash and str(signal_hash).strip() == str(prev_signal_hash).strip():
                            conn.rollback()
                            return (False, f"SAME_STRATEGY_STALE_SIGNAL_REENTRY_BLOCKED ({key[0]}:{key[1]})", None)

                # Insert or Replace into persistent DB
                cur.execute("""
                    INSERT OR REPLACE INTO strategy_lifecycles (
                        strategy_id, symbol, status, entry_price, signal_hash,
                        entry_date, closed_date, exit_reason_code, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    key[0], key[1], StrategyLifecycleState.OPEN, float(entry_price),
                    signal_hash or "", req_date, None, None, now_str, now_str
                ))
                conn.commit()

                committed_record = {
                    "strategy_id": key[0],
                    "symbol": key[1],
                    "status": StrategyLifecycleState.OPEN,
                    "entry_price": float(entry_price),
                    "signal_hash": signal_hash or "",
                    "entry_date": req_date,
                    "created_at": now_str,
                    "updated_at": now_str,
                }
                return (True, "BUY_COMMITTED_SUCCESSFULLY", committed_record)
            except Exception as e:
                conn.rollback()
                return (False, f"TRANSACTION_ERROR ({e})", None)
            finally:
                conn.close()

    def update_lifecycle_state(
        self,
        strategy_id: str,
        symbol: str,
        new_status: str,
        exit_reason_code: Optional[str] = None,
        current_date: Optional[date] = None
    ) -> bool:
        """
        Updates position lifecycle status inside persistent SQLite DB (BUY and EXIT shared transaction boundary).
        """
        key = self._get_key(strategy_id, symbol)
        req_date = (current_date or datetime.now().date()).strftime("%Y-%m-%d")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                cur = conn.cursor()
                cur.execute(
                    "SELECT entry_price, signal_hash, created_at FROM strategy_lifecycles WHERE strategy_id = ? AND symbol = ?",
                    (key[0], key[1])
                )
                row = cur.fetchone()

                entry_price = float(row["entry_price"]) if row else 0.0
                signal_hash = row["signal_hash"] if row else ""
                created_at = row["created_at"] if row else now_str

                closed_date_val = req_date if new_status == StrategyLifecycleState.CLOSED else None

                cur.execute("""
                    INSERT OR REPLACE INTO strategy_lifecycles (
                        strategy_id, symbol, status, entry_price, signal_hash,
                        entry_date, closed_date, exit_reason_code, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    key[0], key[1], new_status, entry_price, signal_hash,
                    req_date, closed_date_val, exit_reason_code or "", created_at, now_str
                ))
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                return False
            finally:
                conn.close()

    def clear_all(self):
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM strategy_lifecycles;")
                conn.commit()


# Backward Compatibility Aliases
SameStrategyLifecycleGate = PersistentStrategyLifecycleGate

_global_lifecycle_gate: Optional[PersistentStrategyLifecycleGate] = None


def get_strategy_lifecycle_gate() -> PersistentStrategyLifecycleGate:
    global _global_lifecycle_gate
    if _global_lifecycle_gate is None:
        _global_lifecycle_gate = PersistentStrategyLifecycleGate()
    return _global_lifecycle_gate
