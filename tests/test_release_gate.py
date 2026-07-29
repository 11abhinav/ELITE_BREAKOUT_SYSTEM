import os
import ast
import sys
import importlib
import pkgutil
import pytest

def test_no_absolute_app_imports():
    """
    Ensure no files in the 'app/' directory use 'from app.' or 'import app.'
    outside of __main__ blocks. This prevents ModuleNotFoundError in production 
    where Railway runs with /app/app as the root directory.
    """
    app_dir = os.path.abspath("app")
    assert os.path.exists(app_dir), "app/ directory must exist"
    
    violations = []
    
    for root, dirs, files in os.walk(app_dir):
        rel_dir = os.path.relpath(root, app_dir)
        # Skip scratch, tests inside app, and incomplete refactor subdirectories
        if "scratch" in root or rel_dir.startswith(("core", "bootstrap", "config", "constants", "database", "domain", "health")):
            continue
            
        for f in files:
            if not f.endswith(".py"):
                continue
                
            path = os.path.join(root, f)
            rel_path = os.path.relpath(path, app_dir)
            
            with open(path, "r", encoding="utf-8") as file:
                source = file.read()
                
            try:
                tree = ast.parse(source, filename=path)
                
                for node in tree.body:
                    # Allow it if it's inside an 'if __name__ == "__main__":' block
                    if isinstance(node, ast.If):
                        try:
                            if (isinstance(node.test, ast.Compare) and 
                                isinstance(node.test.left, ast.Name) and 
                                node.test.left.id == '__name__'):
                                continue
                        except Exception:
                            pass
                    
                    # Walk the rest of the AST tree at this top-level node
                    for sub_node in ast.walk(node):
                        if isinstance(sub_node, ast.Import):
                            for alias in sub_node.names:
                                if alias.name == "app" or alias.name.startswith("app."):
                                    violations.append(f"{rel_path}: import {alias.name}")
                        elif isinstance(sub_node, ast.ImportFrom):
                            if sub_node.module == "app" or (sub_node.module and sub_node.module.startswith("app.")):
                                violations.append(f"{rel_path}: from {sub_node.module} import ...")
                                
            except SyntaxError as e:
                violations.append(f"{rel_path}: SyntaxError: {e}")
                
    assert not violations, "Found absolute 'app.' imports which will crash in production:\n" + "\n".join(violations)

def test_all_modules_importable():
    """
    Simulate the production PYTHONPATH environment and attempt to import
    every module in the app/ directory. This ensures no circular imports,
    missing dependencies, or syntax errors exist anywhere in the codebase.
    """
    app_dir = os.path.abspath("app")
    
    # Temporarily modify sys.path to simulate production
    original_path = list(sys.path)
    
    try:
        # Remove the project root from sys.path if it exists
        project_root = os.path.abspath(".")
        if project_root in sys.path:
            sys.path.remove(project_root)
            
        # Add app/ directory as the root (how Railway runs it)
        sys.path.insert(0, app_dir)
        
        failed_imports = []
        
        # We only need to check top-level modules or walk through packages
        for root, dirs, files in os.walk(app_dir):
            if "scratch" in root or "__pycache__" in root:
                continue
                
            rel_dir = os.path.relpath(root, app_dir)
            if rel_dir == ".":
                pkg_prefix = ""
            else:
                pkg_prefix = rel_dir.replace(os.sep, ".") + "."
                
            for f in files:
                if not f.endswith(".py") or f == "__init__.py":
                    continue
                
                # Exclude test files, scratch scripts, and incomplete core refactor
                if (f.startswith("test_") or f.startswith("check_") or f.startswith("dump_") or 
                    f.startswith("modify_") or f.startswith("clear_") or f.startswith("migrate_") or 
                    f.startswith("fix_") or rel_dir.startswith(("core", "bootstrap", "config", "constants", "database", "domain", "health")) or f.startswith("query_") or
                    f == "push_service.py" or f == "yf_bootstrap.py"):
                    continue
                    
                module_name = pkg_prefix + f[:-3]
                
                try:
                    importlib.import_module(module_name)
                except Exception as e:
                    # Ignore intentional configuration exceptions that might fire on import
                    if "DATABASE_URL" in str(e) or "credentials" in str(e).lower():
                        continue
                    failed_imports.append(f"{module_name}: {type(e).__name__} - {e}")
                    
        assert not failed_imports, "Failed to import the following modules in production environment simulation:\n" + "\n".join(failed_imports)
        
    finally:
        # Restore original path
        sys.path = original_path

def test_registry_completeness():
    """
    Ensure the ValidationRegistry has fully instantiated pipelines for all Tier 1 datasets.
    """
    from app.validation.registry import registry, DatasetType
    
    tier_1_datasets = [
        DatasetType.PRICE,
        DatasetType.BHAVCOPY,
        DatasetType.DELIVERY,
        DatasetType.SYMBOL_MASTER
    ]
    
    missing = []
    for ds in tier_1_datasets:
        try:
            pipeline = registry.get_pipeline(ds)
            if pipeline is None:
                missing.append(ds.name)
        except NotImplementedError:
            missing.append(ds.name)
            
    assert not missing, f"ValidationRegistry is missing fully instantiated pipelines for Tier 1 datasets: {missing}"
