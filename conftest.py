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

# Insert the app/ directory as the *first* entry on sys.path so that bare-name
# imports in tests resolve to app/<module>.py rather than any identically-named
# system package.
_APP_DIR = os.path.join(os.path.dirname(__file__), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
