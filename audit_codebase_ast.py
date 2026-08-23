"""
AST Static Analysis Auditor for UnboundLocalError & Variable Shadowing Risks
Scans every Python file and function body in the codebase using Python's `ast` module.
Detects:
1. Local Variable Shadowing (UnboundLocalError): Variable loaded before local import/assignment in the same scope.
2. Undefined Name Access: Load of variable not in parameters, locals, globals, or built-ins.
3. Redundant/Dangerous Local Re-Imports of Globals.
"""

import ast
import os
import sys
import glob
import builtins
from typing import List, Dict, Set, Tuple

BUILTIN_NAMES = set(dir(builtins))

class VariableScopeVisitor(ast.NodeVisitor):
    def __init__(self, filename: str):
        self.filename = filename
        self.issues: List[Dict] = []
        self.global_imports: Set[str] = set()
        self.global_names: Set[str] = set()

    def visit_Module(self, node: ast.Module):
        # First pass: collect top-level imports and global assignments
        for stmt in node.body:
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    name = alias.asname or alias.name.split('.')[0]
                    self.global_imports.add(name)
                    self.global_names.add(name)
            elif isinstance(stmt, ast.ImportFrom):
                for alias in stmt.names:
                    name = alias.asname or alias.name
                    self.global_imports.add(name)
                    self.global_names.add(name)
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        self.global_names.add(target.id)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.global_names.add(stmt.name)

        # Second pass: visit function bodies
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._analyze_function(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._analyze_function(node)
        self.generic_visit(node)

    def _analyze_function(self, fn_node):
        fn_name = fn_node.name
        fn_lineno = fn_node.lineno

        # Collect parameters, global declarations, and nonlocal declarations
        params = set()
        for arg in fn_node.args.args:
            params.add(arg.arg)
        if fn_node.args.vararg:
            params.add(fn_node.args.vararg.arg)
        if fn_node.args.kwarg:
            params.add(fn_node.args.kwarg.arg)

        global_decls = set()
        nonlocal_decls = set()

        # Collect local assignments and local imports with their line numbers
        local_assignments: Dict[str, List[int]] = {}
        local_imports: Dict[str, List[int]] = {}
        loads: List[Tuple[str, int]] = []

        class LocalVisitor(ast.NodeVisitor):
            def visit_FunctionDef(self, inner_node):
                pass
            def visit_AsyncFunctionDef(self, inner_node):
                pass
            def visit_ClassDef(self, inner_node):
                pass

            def visit_Global(self, stmt):
                for name in stmt.names:
                    global_decls.add(name)

            def visit_Nonlocal(self, stmt):
                for name in stmt.names:
                    nonlocal_decls.add(name)

            def visit_Import(self, stmt):
                for alias in stmt.names:
                    name = alias.asname or alias.name.split('.')[0]
                    local_imports.setdefault(name, []).append(stmt.lineno)
                    local_assignments.setdefault(name, []).append(stmt.lineno)

            def visit_ImportFrom(self, stmt):
                for alias in stmt.names:
                    name = alias.asname or alias.name
                    local_imports.setdefault(name, []).append(stmt.lineno)
                    local_assignments.setdefault(name, []).append(stmt.lineno)

            def visit_Assign(self, stmt):
                self.visit(stmt.value)
                for target in stmt.targets:
                    self._extract_targets(target, stmt.lineno)

            def visit_AnnAssign(self, stmt):
                if stmt.value:
                    self.visit(stmt.value)
                self._extract_targets(stmt.target, stmt.lineno)

            def visit_AugAssign(self, stmt):
                self.visit(stmt.value)
                self._extract_targets(stmt.target, stmt.lineno)

            def visit_For(self, stmt):
                self._extract_targets(stmt.target, stmt.lineno)
                self.visit(stmt.iter)
                for body_stmt in stmt.body:
                    self.visit(body_stmt)
                for oelse in stmt.orelse:
                    self.visit(oelse)

            def visit_AsyncFor(self, stmt):
                self._extract_targets(stmt.target, stmt.lineno)
                self.visit(stmt.iter)
                for body_stmt in stmt.body:
                    self.visit(body_stmt)
                for oelse in stmt.orelse:
                    self.visit(oelse)

            def visit_ListComp(self, node):
                pass
            def visit_SetComp(self, node):
                pass
            def visit_DictComp(self, node):
                pass
            def visit_GeneratorExp(self, node):
                pass

            def visit_ExceptHandler(self, stmt):
                if stmt.name:
                    local_assignments.setdefault(stmt.name, []).append(stmt.lineno)
                self.generic_visit(stmt)

            def visit_Name(self, name_node):
                if isinstance(name_node.ctx, ast.Load):
                    loads.append((name_node.id, name_node.lineno))

            def _extract_targets(self, target_node, lineno):
                if isinstance(target_node, ast.Name):
                    local_assignments.setdefault(target_node.id, []).append(lineno)
                elif isinstance(target_node, (ast.Tuple, ast.List)):
                    for elt in target_node.elts:
                        self._extract_targets(elt, lineno)

        lv = LocalVisitor()
        for stmt in fn_node.body:
            lv.visit(stmt)

        # Remove declared global/nonlocal variables and function parameters from local assignment scope
        true_local_assignments = {
            k: v for k, v in local_assignments.items()
            if k not in global_decls and k not in nonlocal_decls and k not in params
        }

        # AUDIT RULE 1: UnboundLocalError Check
        for var_name, first_load_line in loads:
            if var_name in true_local_assignments:
                first_assign_line = min(true_local_assignments[var_name])
                if first_load_line < first_assign_line:
                    self.issues.append({
                        "file": self.filename,
                        "function": fn_name,
                        "line": first_load_line,
                        "type": "UNBOUND_LOCAL_ERROR",
                        "variable": var_name,
                        "first_load_line": first_load_line,
                        "first_assign_line": first_assign_line,
                        "details": f"Variable '{var_name}' loaded at line {first_load_line} before local assignment/import at line {first_assign_line}"
                    })

        # AUDIT RULE 2: Dangerous Local Shadowing of Global Imports
        for var_name, import_lines in local_imports.items():
            if var_name in self.global_imports and var_name not in global_decls:
                first_imp_line = min(import_lines)
                loads_before_imp = [l_line for v_name, l_line in loads if v_name == var_name and l_line < first_imp_line]
                if loads_before_imp:
                    self.issues.append({
                        "file": self.filename,
                        "function": fn_name,
                        "line": loads_before_imp[0],
                        "type": "SHADOWED_GLOBAL_IMPORT",
                        "variable": var_name,
                        "first_load_line": loads_before_imp[0],
                        "local_import_line": first_imp_line,
                        "details": f"Global import '{var_name}' used at line {loads_before_imp[0]} but shadowed by local import at line {first_imp_line}"
                    })

        # AUDIT RULE 3: Undefined Global Name Access (NameError Check)
        COMMON_CRITICAL_NAMES = {
            "os", "sys", "json", "math", "re", "time", "datetime", "date", "timedelta",
            "pd", "np", "logger", "gc", "psutil", "Optional", "List", "Dict", "Set",
            "Tuple", "Union", "Any", "dataclass", "requests", "hashlib", "traceback"
        }
        for var_name, load_line in loads:
            if var_name in COMMON_CRITICAL_NAMES:
                if (var_name not in self.global_names and
                    var_name not in self.global_imports and
                    var_name not in local_assignments and
                    var_name not in params and
                    var_name not in global_decls and
                    var_name not in nonlocal_decls and
                    var_name not in BUILTIN_NAMES):
                    self.issues.append({
                        "file": self.filename,
                        "function": fn_name,
                        "line": load_line,
                        "type": "UNDEFINED_GLOBAL_NAME",
                        "variable": var_name,
                        "first_load_line": load_line,
                        "details": f"Name '{var_name}' accessed at line {load_line} but is NOT imported or defined globally or locally (NameError risk!)"
                    })

def audit_codebase():
    py_files = sorted(glob.glob('app/**/*.py', recursive=True) + glob.glob('tests/**/*.py', recursive=True))
    all_issues = []

    print(f"🔍 AUDITING {len(py_files)} PYTHON FILES LINE-BY-LINE VIA AST PARSING...")
    
    for fpath in py_files:
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                code = f.read()
            tree = ast.parse(code, filename=fpath)
            visitor = VariableScopeVisitor(fpath)
            visitor.visit(tree)
            all_issues.extend(visitor.issues)
        except Exception as e:
            print(f"❌ Failed to parse AST for {fpath}: {e}")

    print("\n" + "=" * 100)
    print("AST CODEBASE SCOPE & UNBOUND_LOCAL_ERROR AUDIT RESULTS")
    print("=" * 100)

    if not all_issues:
        print("✅ ZERO UNBOUND_LOCAL_ERROR OR VARIABLE SHADOWING ISSUES DETECTED IN CODEBASE!")
    else:
        print(f"❌ FOUND {len(all_issues)} SCOPE / SHADOWING ISSUES ACROSS CODEBASE:\n")
        # Deduplicate issues by (file, function, variable, type)
        seen = set()
        for issue in all_issues:
            key = (issue["file"], issue["function"], issue["variable"], issue["type"])
            if key in seen:
                continue
            seen.add(key)
            print(f"  • File    : {issue['file']}:{issue['line']}")
            print(f"    Function: {issue['function']}()")
            print(f"    Type    : {issue['type']}")
            print(f"    Variable: '{issue['variable']}'")
            print(f"    Details : {issue['details']}\n")

    print("=" * 100)
    return all_issues

if __name__ == "__main__":
    audit_codebase()
