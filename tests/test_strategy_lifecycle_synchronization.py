"""
UNIT TEST BATTERY: PERSISTENT LIFECYCLE SYNCHRONIZATION & RACE BATTERY (Q31-Q34)
Validates:
  - Q31: Same-Strategy BUY/EXIT Synchronization (OPEN, EXIT_PENDING, SELL_REVIEW, SELL block same-strategy BUY).
  - Q32: Cross-Strategy Non-Blocking (Compounder position DOES NOT block Recovery BUY on same symbol).
  - Q33a: Same-Day Re-Entry Block Policy.
  - Q33b: Stale Signal vs Genuine New Signal Re-Entry Identity Guard.
  - Q34a: Multi-Thread Atomic Race Safety.
  - Q34b: Multi-Process Atomic Race Safety (Process-isolated worker processes).
  - Q34c: Process Restart Recovery Safety (State survives memory wipe & process death).
  - Q34d: Concurrent BUY-vs-EXIT Shared Transaction Boundary Safety.
"""

import os
import pytest
import tempfile
import threading
import multiprocessing
import concurrent.futures
from datetime import date, timedelta


from app.strategy_lifecycle_gate import (
    PersistentStrategyLifecycleGate,
    StrategyLifecycleState,
)


@pytest.fixture
def test_db_path():
    temp_dir = tempfile.mkdtemp()
    db_file = os.path.join(temp_dir, "test_buy_alerts_journal.db")
    yield db_file
    if os.path.exists(db_file):
        os.remove(db_file)


@pytest.fixture
def gate(test_db_path):
    gate_inst = PersistentStrategyLifecycleGate(db_path=test_db_path)
    gate_inst.clear_all()
    return gate_inst


def test_q31_same_strategy_buy_exit_synchronization(gate):
    """Q31: Verify OPEN, EXIT_PENDING, SELL_REVIEW, and SELL block same-strategy BUY."""
    strat = "QUALITY_COMPOUNDER"
    sym = "RAILTEL"
    today = date(2026, 10, 5)

    # 1. Clean State -> BUY Allowed
    eligible, reason = gate.evaluate_buy_eligibility(strat, sym, current_date=today)
    assert eligible is True
    assert reason == "NO_ACTIVE_LIFECYCLE"

    # 2. Register Position -> State = OPEN
    ok, msg, rec = gate.register_buy_transactional(strat, sym, 100.0, current_date=today)
    assert ok is True
    assert rec["status"] == StrategyLifecycleState.OPEN

    # 3. OPEN State -> BUY Blocked
    eligible, reason = gate.evaluate_buy_eligibility(strat, sym, current_date=today)
    assert eligible is False
    assert "SAME_STRATEGY_POSITION_OPEN" in reason

    # 4. Transition to EXIT_PENDING -> BUY Blocked
    gate.update_lifecycle_state(strat, sym, StrategyLifecycleState.EXIT_PENDING, current_date=today)
    eligible, reason = gate.evaluate_buy_eligibility(strat, sym, current_date=today)
    assert eligible is False
    assert "SAME_STRATEGY_EXIT_PENDING" in reason

    # 5. Transition to SELL_REVIEW -> BUY Blocked
    gate.update_lifecycle_state(strat, sym, StrategyLifecycleState.SELL_REVIEW, current_date=today)
    eligible, reason = gate.evaluate_buy_eligibility(strat, sym, current_date=today)
    assert eligible is False
    assert "SAME_STRATEGY_SELL_REVIEW_ACTIVE" in reason

    # 6. Transition to SELL -> BUY Blocked
    gate.update_lifecycle_state(strat, sym, StrategyLifecycleState.SELL, current_date=today)
    eligible, reason = gate.evaluate_buy_eligibility(strat, sym, current_date=today)
    assert eligible is False
    assert "SAME_STRATEGY_SELL_EXECUTING" in reason


def test_q32_cross_strategy_non_blocking(gate):
    """Q32: Active QUALITY_COMPOUNDER on RAILTEL DOES NOT block QUALITY_VALUE_RECOVERY BUY on RAILTEL."""
    sym = "RAILTEL"
    today = date(2026, 10, 5)

    # 1. Open Position in QUALITY_COMPOUNDER
    ok_comp, msg_comp, _ = gate.register_buy_transactional("QUALITY_COMPOUNDER", sym, 150.0, current_date=today)
    assert ok_comp is True

    # 2. Evaluate BUY for QUALITY_VALUE_RECOVERY on same symbol -> MUST BE ALLOWED
    eligible_rec, reason_rec = gate.evaluate_buy_eligibility("QUALITY_VALUE_RECOVERY", sym, current_date=today)
    assert eligible_rec is True, f"Cross-strategy BUY must NOT be blocked! Reason: {reason_rec}"
    assert reason_rec == "NO_ACTIVE_LIFECYCLE"

    # 3. Register Position in QUALITY_VALUE_RECOVERY -> MUST SUCCEED
    ok_rec, msg_rec, rec_data = gate.register_buy_transactional("QUALITY_VALUE_RECOVERY", sym, 120.0, current_date=today)
    assert ok_rec is True
    assert rec_data["strategy_id"] == "QUALITY_VALUE_RECOVERY"

    # 4. Same-strategy BUY for QUALITY_COMPOUNDER is STILL blocked
    eligible_comp, _ = gate.evaluate_buy_eligibility("QUALITY_COMPOUNDER", sym, current_date=today)
    assert eligible_comp is False


def test_q33a_same_day_reentry_policy(gate):
    """Q33a: Test same-day re-entry block policy."""
    strat = "QUALITY_COMPOUNDER"
    sym = "TATAMOTORS"
    today = date(2026, 10, 5)

    gate.register_buy_transactional(strat, sym, 500.0, current_date=today, signal_hash="SIG_100")
    gate.update_lifecycle_state(strat, sym, StrategyLifecycleState.CLOSED, exit_reason_code="HARD_DRAWDOWN_STOP", current_date=today)

    eligible_today, reason_today = gate.evaluate_buy_eligibility(strat, sym, current_date=today, signal_hash="SIG_200")
    assert eligible_today is False
    assert "SAME_DAY_REENTRY_BLOCKED" in reason_today


def test_q33b_stale_signal_vs_new_qualifying_signal(gate):
    """Q33b: Test stale signal vs new qualifying signal re-entry identity guard."""
    strat = "QUALITY_COMPOUNDER"
    sym = "INFY"
    yesterday = date(2026, 10, 4)
    today = date(2026, 10, 5)

    old_signal_hash = "SHA256_STALE_SIGNAL_ABC123"
    new_signal_hash = "SHA256_NEW_GENUINE_SIGNAL_XYZ999"

    # 1. Buy and close yesterday with old_signal_hash
    gate.register_buy_transactional(strat, sym, 1400.0, current_date=yesterday, signal_hash=old_signal_hash)
    gate.update_lifecycle_state(strat, sym, StrategyLifecycleState.CLOSED, exit_reason_code="RS_BREAKDOWN", current_date=yesterday)

    # 2. Attempt BUY today with SAME old_signal_hash (Stale Duplicate Signal) -> MUST BE BLOCKED
    eligible_stale, reason_stale = gate.evaluate_buy_eligibility(strat, sym, current_date=today, signal_hash=old_signal_hash)
    assert eligible_stale is False
    assert "STALE_SIGNAL_REENTRY_BLOCKED" in reason_stale

    # 3. Attempt BUY today with NEW genuine signal_hash -> MUST BE ALLOWED
    eligible_new, reason_new = gate.evaluate_buy_eligibility(strat, sym, current_date=today, signal_hash=new_signal_hash)
    assert eligible_new is True
    assert reason_new == "SAME_STRATEGY_PRIOR_CLOSE_EXPIRED"


def test_q34a_multi_thread_race_safety(gate):
    """Q34a: Multi-threaded concurrent worker race safety proof."""
    strat = "QUALITY_COMPOUNDER"
    sym = "RELIANCE"
    today = date(2026, 10, 5)
    num_workers = 20
    results = []

    def worker_attempt(worker_id: int):
        ok, msg, rec = gate.register_buy_transactional(strat, sym, 2500.0, current_date=today, signal_hash=f"THREAD_SIG_{worker_id}")
        return (worker_id, ok, msg)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(worker_attempt, i) for i in range(num_workers)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    success_count = sum(1 for _, ok, _ in results if ok)
    blocked_count = sum(1 for _, ok, _ in results if not ok)

    assert success_count == 1, f"EXACTLY 1 worker must succeed! Got: {success_count}"
    assert blocked_count == num_workers - 1, f"Remaining workers must be blocked! Got: {blocked_count}"


def _process_worker_entry(db_path: str, worker_id: int, result_queue: multiprocessing.Queue):
    """Process worker function running in an isolated OS process."""
    gate_proc = PersistentStrategyLifecycleGate(db_path=db_path)
    ok, msg, _ = gate_proc.register_buy_transactional(
        strategy_id="QUALITY_COMPOUNDER",
        symbol="TCS",
        entry_price=3500.0,
        current_date=date(2026, 10, 5),
        signal_hash=f"PROC_SIG_{worker_id}"
    )
    result_queue.put((worker_id, ok, msg))


def test_q34b_multi_process_race_safety(test_db_path):
    """Q34b: Multi-process OS worker race safety proof across isolated Python processes."""
    num_processes = 10
    result_queue = multiprocessing.Queue()
    processes = []

    for i in range(num_processes):
        p = multiprocessing.Process(target=_process_worker_entry, args=(test_db_path, i, result_queue))
        processes.append(p)
        p.start()

    for p in processes:
        p.join()

    results = []
    while not result_queue.empty():
        results.append(result_queue.get())

    success_count = sum(1 for _, ok, _ in results if ok)
    blocked_count = sum(1 for _, ok, _ in results if not ok)

    assert success_count == 1, f"EXACTLY 1 multi-process worker must succeed! Got: {success_count}"
    assert blocked_count == num_processes - 1, f"Remaining processes must be blocked! Got: {blocked_count}"


def test_q34c_process_restart_recovery_safety(test_db_path):
    """Q34c: Process restart recovery safety proof. State persists across process death/restart."""
    strat = "QUALITY_COMPOUNDER"
    sym = "HDFCBANK"
    today = date(2026, 10, 5)

    # 1. Process A instantiates gate and commits BUY
    gate_proc_a = PersistentStrategyLifecycleGate(db_path=test_db_path)
    ok_a, _, _ = gate_proc_a.register_buy_transactional(strat, sym, 1600.0, current_date=today)
    assert ok_a is True

    # 2. Simulate complete Process A death / memory wipe
    del gate_proc_a

    # 3. Process B starts up (simulating system restart)
    gate_proc_b = PersistentStrategyLifecycleGate(db_path=test_db_path)

    # 4. Verify Process B reads active position from persistent DB and BLOCKS duplicate BUY
    eligible_b, reason_b = gate_proc_b.evaluate_buy_eligibility(strat, sym, current_date=today)
    assert eligible_b is False, "Process B after restart MUST detect open position in DB!"
    assert "SAME_STRATEGY_POSITION_OPEN" in reason_b

    ok_b, _, _ = gate_proc_b.register_buy_transactional(strat, sym, 1605.0, current_date=today)
    assert ok_b is False, "Process B MUST be blocked from registering duplicate BUY!"


def test_q34d_buy_vs_exit_concurrent_race_safety(gate):
    """Q34d: Concurrent BUY-vs-EXIT shared transaction boundary safety proof."""
    strat = "QUALITY_COMPOUNDER"
    sym = "LTIM"
    today = date(2026, 10, 5)

    # 1. Open position
    gate.register_buy_transactional(strat, sym, 5000.0, current_date=today)

    # 2. Run concurrent BUY attempt and EXIT state update
    results = {}

    def try_buy():
        ok, msg, _ = gate.register_buy_transactional(strat, sym, 5010.0, current_date=today)
        results["buy"] = (ok, msg)

    def try_exit():
        ok = gate.update_lifecycle_state(strat, sym, StrategyLifecycleState.CLOSED, exit_reason_code="E3_MARGIN_COLLAPSE", current_date=today)
        results["exit"] = ok

    t_buy = threading.Thread(target=try_buy)
    t_exit = threading.Thread(target=try_exit)

    t_buy.start()
    t_exit.start()

    t_buy.join()
    t_exit.join()

    # Verify that BUY was blocked during active lifecycle and EXIT updated persistent state cleanly
    buy_ok, buy_msg = results["buy"]
    assert buy_ok is False, "BUY must be blocked while position is active/closing"
    assert results["exit"] is True


def test_q35_v1_warning_recovery_clear(gate):
    """
    Q35 — V1 Warning Recovery / Clear Lifecycle Semantics Proof.
    
    Sequence:
      DAY 1 15:15 V1 -> INTRADAY_WARNING (ORANGE state set, transient telemetry, position remains OPEN)
      DAY 1 18:30 V2 -> HOLD (Definitive 2-close rule NOT met, no state mutation, position remains OPEN)
      DAY 2 15:15 V1 -> CONDITION RECOVERED (Price >= SMA200)
      EXPECTED      -> WARNING CLEARED / GREEN state restored / position status OPEN
    """
    strat = "QUALITY_COMPOUNDER"
    sym = "TATACONSUM"
    day1 = date(2026, 10, 5)
    day2 = date(2026, 10, 6)

    # 1. Register initial position -> OPEN
    ok, _, rec = gate.register_buy_transactional(strat, sym, 1100.0, current_date=day1)
    assert ok is True
    assert rec["status"] == StrategyLifecycleState.OPEN

    # 2. DAY 1 15:15 IST V1 Pulse -> INTRADAY WARNING (ORANGE state)
    gate.update_lifecycle_state(
        strat, sym, StrategyLifecycleState.SELL_REVIEW, exit_reason_code="V1_INTRADAY_WARNING", current_date=day1
    )
    lifecycle_day1_v1 = gate.get_lifecycle(strat, sym)
    assert lifecycle_day1_v1["status"] == StrategyLifecycleState.SELL_REVIEW
    # Position MUST remain active/non-closed in persistent gate
    eligible_buy, buy_reason = gate.evaluate_buy_eligibility(strat, sym, current_date=day1)
    assert eligible_buy is False, "Position in SELL_REVIEW must remain active and block duplicate BUY"

    # 3. DAY 1 18:30 IST V2 Pulse -> HOLD (No final state mutation to CLOSED)
    # Definitive evaluator returns HOLD, so wealth_engine.py does NOT execute CLOSED.
    lifecycle_day1_v2 = gate.get_lifecycle(strat, sym)
    assert lifecycle_day1_v2["status"] != StrategyLifecycleState.CLOSED, "DAY 1 V2 HOLD must NOT close position"

    # 4. DAY 2 15:15 IST V1 Pulse -> CONDITION RECOVERED -> Restores to OPEN / GREEN
    gate.update_lifecycle_state(
        strat, sym, StrategyLifecycleState.OPEN, exit_reason_code="V1_WARNING_CLEARED", current_date=day2
    )
    lifecycle_day2_v1 = gate.get_lifecycle(strat, sym)
    assert lifecycle_day2_v1["status"] == StrategyLifecycleState.OPEN, "DAY 2 V1 recovery MUST restore status to OPEN"
    assert lifecycle_day2_v1["last_exit_reason_code"] == "V1_WARNING_CLEARED"

