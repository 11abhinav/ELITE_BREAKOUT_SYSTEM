import ast
import os
import logging

logger = logging.getLogger(__name__)

class ArchitectureValidator:
    """
    Validates that core architectural invariants are not violated by performance optimizations.
    Uses AST parsing to detect forbidden patterns (e.g., yf.download inside scanners).
    """
    
    FORBIDDEN_CALLS_IN_SCANNERS = [
        "yf.download",
        "requests.get",
        "json.loads", # Catch raw I/O parsing in tight loops
        "pd.read_csv",
        "pd.read_parquet"
    ]
    
    @classmethod
    def validate_scanners(cls) -> bool:
        scanner_dir = "app/"
        valid = True
        
        for filename in os.listdir(scanner_dir):
            if not filename.endswith("_scanner.py") and filename != "scoring_engine.py":
                continue
                
            filepath = os.path.join(scanner_dir, filename)
            if not os.path.isfile(filepath):
                continue
                
            with open(filepath, 'r') as f:
                source = f.read()
                
            try:
                tree = ast.parse(source)
            except SyntaxError:
                logger.error(f"❌ ArchitectureValidator: Syntax error in {filepath}")
                valid = False
                continue
                
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    # Check for attribute calls like module.function()
                    if isinstance(node.func, ast.Attribute):
                        if isinstance(node.func.value, ast.Name):
                            call_name = f"{node.func.value.id}.{node.func.attr}"
                            if call_name in cls.FORBIDDEN_CALLS_IN_SCANNERS:
                                logger.error(f"❌ ArchitectureValidator: Forbidden call '{call_name}' found in {filename} at line {node.lineno}")
                                valid = False
                    # Check for direct calls
                    elif isinstance(node.func, ast.Name):
                        if node.func.id in cls.FORBIDDEN_CALLS_IN_SCANNERS:
                            logger.error(f"❌ ArchitectureValidator: Forbidden call '{node.func.id}' found in {filename} at line {node.lineno}")
                            valid = False
                            
        if valid:
            logger.info("✅ ArchitectureValidator: No violations found in scanners.")
            
        return valid
