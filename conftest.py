"""
conftest.py — Root pytest configuration
========================================
Inserts the `app/` directory into sys.path so that all test modules can import
application modules using bare names (e.g. `from eod_v2_engine import ...`).

WHY THIS IS HERE
----------------
The test suite was authored before the `app/` package structure was formalised.
All test modules use flat bare-name imports (e.g. `from alert_quality_engine import ...`)
rather than package-qualified imports (e.g. `from app.alert_quality_engine import ...`).
Adding `app/` to sys.path here is the zero-impact fix — it avoids having to
rewrite all existing test imports while keeping the package layout intact.
"""

import os
import sys

_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

_APP_DIR = os.path.join(_ROOT_DIR, "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
