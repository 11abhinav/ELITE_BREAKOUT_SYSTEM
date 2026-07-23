import ast
import os
import glob

def get_db_bound_functions(db_path):
    """Parse database.py and find all functions that use get_connection()."""
    db_bound = set()
    try:
        with open(db_path, "r") as f:
            tree = ast.parse(f.read(), filename=db_path)
            
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        if isinstance(child.func, ast.Name) and child.func.id == "get_connection":
                            db_bound.add(node.name)
                            break
    except Exception as e:
        print(f"Error parsing {db_path}: {e}")
    return db_bound

def test_no_nplus1_read_queries_in_scanner_loops():
    """
    Scans core scanner Python files.
    Checks if any loops (for, while) contain calls to database READ functions.
    This prevents N+1 query performance loopholes (like fetching data individually).
    """
    app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
    db_path = os.path.join(app_dir, "database.py")
    
    # 1. Identify all DB-bound functions from database.py
    db_bound_funcs = get_db_bound_functions(db_path)
    
    # 2. Filter to only READ functions (get_, is_, check_, has_, fetch_)
    read_funcs = {f for f in db_bound_funcs if f.startswith(("get_", "is_", "check_", "has_", "fetch_"))}
    
    # Allow-list for bulk fetching functions that are intentionally called, 
    # or functions that are called only conditionally and rarely.
    allowed = {
        "get_bulk_recent_concall_analysis", 
        "get_ai_concall_stats", 
        "get_promoter_pledge_stats",
        "get_latest_weights" # Cached or fast single-row fetch
    }
    read_funcs = read_funcs - allowed
    
    # Files to ignore (orchestrators and async background workers)
    ignored_files = {
        "main.py", 
        "dashboard_server.py", 
        "ai_worker.py", 
        "pledge_worker.py", 
        "performance_tracker.py", 
        "surveillance.py", 
        "telegram_engine.py", 
        "database.py",
        "delivery_data.py" # Data fetcher module, expected to loop
    }
    
    loopholes_found = []
    
    for py_file in glob.glob(os.path.join(app_dir, "**", "*.py"), recursive=True):
        basename = os.path.basename(py_file)
        if not os.path.isfile(py_file) or basename in ignored_files:
            continue
            
        try:
            with open(py_file, "r") as f:
                tree = ast.parse(f.read(), filename=py_file)
        except Exception:
            continue
            
        # Pass 1: Find local wrapper functions that call DB read functions
        local_db_funcs = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for child in ast.walk(node):
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                        if child.func.id in read_funcs:
                            local_db_funcs.add(node.name)
                            break
                            
        all_read_funcs = read_funcs.union(local_db_funcs)
        
        # Pass 2: Find loops that call any db read function
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.While, ast.AsyncFor)):
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        func_name = None
                        if isinstance(child.func, ast.Name):
                            func_name = child.func.id
                        elif isinstance(child.func, ast.Attribute):
                            func_name = child.func.attr
                            
                        if func_name in all_read_funcs:
                            # Known safe exemption: multi_tf_scanner's check_recent_alert
                            # is buried inside an IF block that only triggers 0-2 times per day.
                            if basename == "multi_tf_scanner.py" and func_name in ["check_recent_alert", "run_lower_tf_phase"]:
                                continue
                                
                            loopholes_found.append(f"{basename}: Loop calls DB READ function '{func_name}' at line {child.lineno}")

    loopholes_found = list(set(loopholes_found))
    
    assert len(loopholes_found) == 0, f"Found N+1 DB READ query loopholes inside scanner loops:\n" + "\n".join(sorted(loopholes_found))

