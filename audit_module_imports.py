"""
Module Import Validation Auditor
Imports every Python module in `app/` and `tests/` to verify zero ImportError, ModuleNotFoundError, or top-level NameError issues.
"""

import os
import sys
import glob
import importlib

def test_imports():
    sys.path.insert(0, 'app')
    py_files = sorted(glob.glob('app/**/*.py', recursive=True) + glob.glob('tests/**/*.py', recursive=True))
    
    print(f"🔍 TESTING IMPORTS FOR {len(py_files)} MODULES...")
    
    failed_imports = []
    
    for fpath in py_files:
        if fpath.endswith('audit_codebase_ast.py') or fpath.endswith('audit_module_imports.py'):
            continue
        rel_path = fpath.replace('/', '.').replace('\\', '.')
        if rel_path.endswith('.py'):
            rel_path = rel_path[:-3]
        
        # Format module name
        mod_name = rel_path
        if mod_name.startswith('app.'):
            mod_name = mod_name[4:]
            
        try:
            importlib.import_module(mod_name)
        except Exception as e:
            failed_imports.append((fpath, mod_name, str(e)))
            
    print("\n" + "=" * 100)
    print("MODULE IMPORT AUDIT RESULTS")
    print("=" * 100)
    
    if not failed_imports:
        print("✅ ALL MODULES IMPORTED SUCCESSFULLY WITH ZERO ERRORS!")
    else:
        print(f"❌ FOUND {len(failed_imports)} IMPORT FAILURES:\n")
        for fpath, mod_name, err in failed_imports:
            print(f"  • File  : {fpath}")
            print(f"    Module: {mod_name}")
            print(f"    Error : {err}\n")
            
    print("=" * 100)
    return failed_imports

if __name__ == "__main__":
    test_imports()
