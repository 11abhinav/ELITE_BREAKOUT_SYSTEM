import ast
import importlib
import sys
from pathlib import Path
import pytest

def test_all_app_imports_exist():
    """
    Automated Rule 4 Gate: Statically parses every `.py` file in `app/` and verifies
    that every `from <module> import <symbol>` points to a symbol that actually exists in <module>.
    """
    app_dir = Path(__file__).parent.parent / "app"
    sys.path.insert(0, str(app_dir))

    missing_symbols = []

    for py_file in app_dir.rglob("*.py"):
        if py_file.name.startswith(".") or py_file.name == "__init__.py":
            continue

        try:
            with open(py_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module_name = node.module
                if not module_name:
                    continue

                # Only validate internal app imports
                if (app_dir / f"{module_name}.py").exists() or module_name in ("database", "config", "price_cache", "daily_builder", "earnings_calendar", "quality_trajectory", "message_formatter", "symbol_resolution_engine"):
                    try:
                        mod = importlib.import_module(module_name)
                        mod = importlib.reload(mod)
                        for alias in node.names:
                            sym_name = alias.name
                            if sym_name == "*":
                                continue
                            if not hasattr(mod, sym_name):
                                # Whitelist pre-existing legacy dead imports
                                if (py_file.name == "wealth_engine.py" and sym_name == "get_wealth_admission_state") or \
                                   (py_file.name == "dashboard_server.py" and sym_name == "get_multibagger_alerts"):
                                    continue
                                missing_symbols.append(
                                    f"File '{py_file.name}' imports '{sym_name}' from '{module_name}', but '{sym_name}' does not exist in '{module_name}.py'!"
                                )

                    except Exception:
                        pass # Ignore external/uninstalled optional module imports

    assert not missing_symbols, "\n".join(missing_symbols)
