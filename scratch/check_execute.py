import ast
import re

with open('app/database.py', 'r') as f:
    tree = ast.parse(f.read())

for node in ast.walk(tree):
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'execute':
            if len(node.args) == 2:
                query_node = node.args[0]
                params_node = node.args[1]
                
                query_str = None
                if isinstance(query_node, ast.Constant) and isinstance(query_node.value, str):
                    query_str = query_node.value
                elif isinstance(query_node, ast.JoinedStr):
                    # F-string, just try to extract the literal parts
                    query_str = "".join([n.value for n in query_node.values if isinstance(n, ast.Constant) and isinstance(n.value, str)])
                    
                if query_str:
                    num_placeholders = query_str.count('%s')
                    
                    if isinstance(params_node, ast.Tuple):
                        num_params = len(params_node.elts)
                        if num_placeholders != num_params:
                            print(f"Line {node.lineno}: Mismatch! {num_placeholders} %s vs {num_params} tuple elements")
                            print(f"Query: {query_str}")
