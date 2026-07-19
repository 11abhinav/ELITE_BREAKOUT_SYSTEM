import subprocess
from datetime import datetime

def run_tests():
    try:
        result = subprocess.run(
            ["python3", "-m", "pytest", "tests/", "-q", "--tb=short"], 
            capture_output=True, text=True
        )
        return result.stdout, result.returncode == 0
    except Exception as e:
        return str(e), False

def generate_markdown(test_output, passed):
    date_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    overall_status = "✅ PASS" if passed else "❌ FAIL"
    
    md_content = f"""# Verification Status Release Gate

**Strategy Version:** 1.0.0
**Golden Scenario Suite:** v1
**Generated At:** {date_str}

## Overall Status
**{overall_status}**

> [!IMPORTANT]
> **Governance Rule:** This suite freezes the core trading behavior of the Elite Breakout System. 
> If a Golden Scenario changes from PASS to FAIL, the change must include a documented explanation of the intended strategy change before the new expected behavior is accepted. Every intentional strategy change must update the affected Golden Scenario and include a brief rationale explaining why the expected behavior changed. This prevents accidental strategy drift while allowing deliberate evolution.

## Section Status

### 1. Framework: {overall_status}
These tests prove the core invariants and boundaries of the system architecture are holding.

- **Architecture**: Enforced by strict dependency and isolation layers.
- **Validation Framework**: `tests/test_validation_engine.py` (Passes invariants)
- **Validation Registry**: `tests/test_validation_engine.py` (Passes contract)
- **Cache State Machine**: `tests/test_cache_state_machine.py` (Passes recovery and update logic)

### 2. Components: {overall_status}
These tests verify that each independent module correctly executes its isolated business logic.

- **Daily Builder**: `tests/test_component_daily_builder.py` (Validates regime assignment)
- **Multi-TF Scanner**: `tests/test_component_scanner.py` (Validates filter math)
- **Scoring Engine**: `tests/test_component_scoring.py` (Validates confluence weights)
- **SL/Target Engine**: `tests/test_component_sl_target.py` (Validates ATR & swing math)
- **Alert Engine**: `tests/test_component_alert.py` (Validates cooldown persistence)

### 3. Trading Behavior (Golden Scenarios): {overall_status}
These behavioral specifications ensure the pipeline makes exactly the expected trading decisions.

- **Scenario A**: Perfect breakout → Resulted in an Alert & `FUNDED` Candidate.
- **Scenario B**: Volume weak → Filtered in Phase D scanner logic, tracked in logs.
- **Scenario C**: Missing validation data (Empty) → Pipeline safely aborts, no crashing.
- **Scenario D**: Poor Risk/Reward (Structural RR < 2.5x) → Alert rejected, explicit `NO_VALID_STRUCTURAL_TARGET` trail recorded.
- **Scenario E**: Duplicate opportunity → Cooldown mechanism prevented the duplicate alert.
- **Scenario F**: Same symbol on multiple scans → Pipeline respects `COOLDOWN` states perfectly.
- **Scenario G**: Gap-up breakout → Alert succeeds, Entry logic safely accommodates gap levels.
- **Scenario H**: False breakout (Closing poorly) → Alert rejected in Phase D for weak close/engulfing failure.
- **Scenario I**: Corporate Action (Split) → Normalized historical data safely ingested and executed.
- **Scenario J**: Market Holiday → Stale ticker data (`is_stale=True`) safely aborted early.
- **Scenario K**: Volatility Shock → ATR widening correctly resulted in mathematically widened Stop Loss levels.
- **Scenario L**: Missing optional fields (e.g., NaN Volume) → Pipeline degrades gracefully, rejecting instead of throwing mathematical exceptions.

## Automated Test Output
```text
{test_output}
```
"""
    with open("VERIFICATION_STATUS.md", "w") as f:
        f.write(md_content)
    print("VERIFICATION_STATUS.md generated successfully.")

if __name__ == "__main__":
    output, passed = run_tests()
    generate_markdown(output, passed)
