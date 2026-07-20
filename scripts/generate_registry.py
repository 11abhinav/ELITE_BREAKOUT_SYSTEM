import os
import sys

# Add project root and app to sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, root_dir)
sys.path.insert(0, os.path.join(root_dir, "app"))

from core.invariants import Invariant
import core.invariants as module_invariants
import core.pipeline_invariants as module_pipeline_invariants

def _get_all_subclasses(cls):
    subclasses = set(cls.__subclasses__())
    for s in list(subclasses):
        subclasses.update(_get_all_subclasses(s))
    return subclasses

def generate_registry():
    out_path = os.path.join(root_dir, "INVARIANT_REGISTRY.md")
    
    invariants = []
    # Make sure all modules containing invariants are imported so subclasses are registered
    
    for inv_cls in _get_all_subclasses(Invariant):
        if hasattr(inv_cls, "id") and inv_cls.id != "INV-UNKNOWN":
            invariants.append(inv_cls)
            
    # Sort by ID
    invariants.sort(key=lambda x: x.id)
    
    with open(out_path, "w") as f:
        f.write("# ELITE Breakout System - Invariant Registry\n\n")
        f.write("This document is automatically generated from the codebase. It lists all executable business invariants enforcing the pipeline's behavior.\n\n")
        f.write("| ID | Severity | Business Rule | Snapshot Stage | Owner | Tests |\n")
        f.write("|---|---|---|---|---|---|\n")
        
        for inv in invariants:
            tests_str = ", ".join(inv.tests) if inv.tests else "N/A"
            f.write(f"| `{inv.id}` | `{inv.severity.value}` | {inv.business_rule} | `{inv.snapshot}` | {inv.owner} | `{tests_str}` |\n")
            
    print(f"✅ Generated {out_path} with {len(invariants)} registered invariants.")

if __name__ == "__main__":
    generate_registry()
