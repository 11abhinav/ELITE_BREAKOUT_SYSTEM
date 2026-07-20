#!/usr/bin/env python3
"""
scripts/traceability.py

Generates traceability_report.md and traceability_report.json by parsing
the BUSINESS_RULES.md, codebase (# Rule: [ID]), and tests ("Rules: [ID]").
Exits with code 1 if CI validation fails (orphan rules, missing impl, etc).
"""

import os
import re
import json
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_FILE = os.path.join(ROOT_DIR, 'docs', 'governance', 'BUSINESS_RULES.md')
APP_DIR = os.path.join(ROOT_DIR, 'app')
TESTS_DIR = os.path.join(ROOT_DIR, 'tests')

# Regex patterns
RULE_DEF_PATTERN = re.compile(r'###\s+\[([A-Z]+-\d+)\]\s+(.*)')
LIFECYCLE_PATTERN = re.compile(r'\*\s+\*\*Lifecycle:\*\*\s+(.*)')
IMPL_ANNOTATION = re.compile(r'#\s*Rule:\s*([A-Z]+-\d+)')
TEST_DOCSTRING = re.compile(r'Rules:\s*\n((?:\s*[A-Z]+-\d+\s*\n)+)')
TEST_DOCSTRING_SINGLE = re.compile(r'[A-Z]+-\d+')

def parse_business_rules():
    rules = {}
    if not os.path.exists(RULES_FILE):
        return rules
    
    with open(RULES_FILE, 'r') as f:
        lines = f.readlines()
        
    current_rule = None
    for line in lines:
        match_def = RULE_DEF_PATTERN.search(line)
        if match_def:
            current_rule = match_def.group(1)
            rules[current_rule] = {
                "id": current_rule,
                "name": match_def.group(2).strip(),
                "lifecycle": "Unknown",
                "implementations": [],
                "tests": []
            }
            continue
            
        if current_rule:
            match_lc = LIFECYCLE_PATTERN.search(line)
            if match_lc:
                rules[current_rule]["lifecycle"] = match_lc.group(1).strip()
    return rules

def find_implementations(rules):
    # Walk app dir
    for root, _, files in os.walk(APP_DIR):
        for file in files:
            if not file.endswith('.py'):
                continue
            path = os.path.join(root, file)
            rel_path = os.path.relpath(path, ROOT_DIR)
            with open(path, 'r', encoding='utf-8') as f:
                for i, line in enumerate(f):
                    match = IMPL_ANNOTATION.search(line)
                    if match:
                        rule_id = match.group(1)
                        if rule_id in rules:
                            rules[rule_id]["implementations"].append(f"{rel_path}:{i+1}")
                        else:
                            # Orphan implementation!
                            if rule_id not in rules:
                                rules[rule_id] = {"id": rule_id, "name": "ORPHAN (Not in Registry)", "lifecycle": "Unknown", "implementations": [], "tests": []}
                            rules[rule_id]["implementations"].append(f"{rel_path}:{i+1}")

def find_tests(rules):
    for root, _, files in os.walk(TESTS_DIR):
        for file in files:
            if not file.endswith('.py'):
                continue
            path = os.path.join(root, file)
            rel_path = os.path.relpath(path, ROOT_DIR)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            matches = TEST_DOCSTRING.findall(content)
            for block in matches:
                rule_ids = TEST_DOCSTRING_SINGLE.findall(block)
                for rule_id in rule_ids:
                    if rule_id in rules:
                        if rel_path not in rules[rule_id]["tests"]:
                            rules[rule_id]["tests"].append(rel_path)
                    else:
                        if rule_id not in rules:
                            rules[rule_id] = {"id": rule_id, "name": "ORPHAN (Not in Registry)", "lifecycle": "Unknown", "implementations": [], "tests": []}
                        if rel_path not in rules[rule_id]["tests"]:
                            rules[rule_id]["tests"].append(rel_path)

def generate_reports():
    rules = parse_business_rules()
    find_implementations(rules)
    find_tests(rules)
    
    commit_hash = os.popen('git rev-parse --short HEAD').read().strip()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    

    # Load approved review items (GR documents)
    gr_dir = os.path.join(ROOT_DIR, 'docs', 'governance', 'reviews')
    approved_drifts = {}
    if os.path.exists(gr_dir):
        for file in os.listdir(gr_dir):
            if file.endswith('.md'):
                with open(os.path.join(gr_dir, file), 'r', encoding='utf-8') as f:
                    c = f.read()
                    r_match = re.search(r'Rule ID:\*?\*?\s*([A-Z]+-\d+)', c)
                    d_match = re.search(r'Disposition:\s*(.*)', c)
                    rec_match = re.search(r'Recommended Action:\s*(.*)', c)
                    tar_match = re.search(r'Target (?:Version|Release):\s*(.*)', c)
                    exp_match = re.search(r'Review Deadline:\s*(.*)', c)
                    id_match = re.search(r'Review ID:\s*(.*)', c)
                    
                    if r_match:
                        rid = r_match.group(1).strip()
                        approved_drifts[rid] = {
                            "id": id_match.group(1).strip().strip("*").strip() if id_match else file.replace(".md", ""),
                            "disposition": d_match.group(1).strip().strip("*").strip() if d_match else "Under Review",
                            "decision": rec_match.group(1).strip().strip("*").strip() if rec_match else "TBD",
                            "target": tar_match.group(1).strip().strip("*").strip() if tar_match else "TBD",
                            "deadline": exp_match.group(1).strip().strip("*").strip() if exp_match else "2099-12-31"
                        }

    # Generate KNOWN_DRIFTS.md
    drifts_md = [
        "# Known Governance Drifts",
        "",
        "| ID | Rule | Status | Decision | Target |",
        "|---|---|---|---|---|"
    ]
    for rid, info in sorted(approved_drifts.items(), key=lambda x: x[1]['id']):
        drifts_md.append(f"| {info['id']} | {rid} | {info['disposition']} | {info['decision']} | {info['target']} |")
        
    os.makedirs(os.path.join(ROOT_DIR, 'docs', 'governance'), exist_ok=True)
    with open(os.path.join(ROOT_DIR, 'docs', 'governance', 'KNOWN_DRIFTS.md'), 'w') as f:
        f.write("\n".join(drifts_md) + "\n")

    has_unapproved_drifts = False
    drift_msgs = []
    
    now_date = datetime.now().strftime('%Y-%m-%d')

    for rid in sorted(rules.keys()):
        data = rules[rid]
        is_frozen = (data.get("lifecycle") == "Frozen")
        is_orphan = "ORPHAN" in data.get("name", "")
        
        # Check expiry
        gr_info = approved_drifts.get(rid)
        is_expired = False
        if gr_info and gr_info['deadline'] < now_date:
            is_expired = True
            
        if is_orphan:
            if not gr_info or is_expired:
                has_unapproved_drifts = True
            decision = f"Expired (Deadline: {gr_info['deadline']})" if is_expired else ("Approved Review Item" if gr_info else "Pending Review")
            drift_msgs.append(
                f"Governance Drift Report\n\n"
                f"Rule: {rid}\n"
                f"Specification Status: Orphan\n"
                f"Implementation: Exists but no registry entry\n"
                f"Classification: Specification/Registry Drift\n"
                f"Decision: {decision}\n"
            )
            
        if is_frozen:
            impl_missing = len(data["implementations"]) == 0
            test_missing = len(data["tests"]) == 0
            
            if impl_missing or test_missing:
                if not gr_info or is_expired:
                    has_unapproved_drifts = True
                decision = f"Expired (Deadline: {gr_info['deadline']})" if is_expired else ("Approved Review Item" if gr_info else "Pending Review")
                
                if impl_missing and test_missing:
                    missing_type = "Implementation and Test"
                    impl_text = "No matching implementation or tests found"
                elif impl_missing:
                    missing_type = "Implementation"
                    impl_text = "No matching implementation found"
                else:
                    missing_type = "Test"
                    impl_text = "No matching tests found"
                
                drift_msgs.append(
                    f"Governance Drift Report\n\n"
                    f"Rule: {rid}\n"
                    f"Specification Status: Frozen\n"
                    f"Implementation: {impl_text}\n"
                    f"Classification: Specification/{missing_type} Drift\n"
                    f"Decision: {decision}\n"
                )
        # JSON Output
    out_json = {
        "metadata": {
            "version": "1.0",
            "generated": now,
            "commit": commit_hash,
            "status": "FAIL" if has_unapproved_drifts else "PASS",
            "drifts": len(drift_msgs)
        },
        "rules": rules
    }
    
    with open(os.path.join(ROOT_DIR, 'traceability_report.json'), 'w') as f:
        json.dump(out_json, f, indent=2)
        
    # MD Output
    md = [
        "# Traceability Report",
        f"**Governance Version:** 1.0  ",
        f"**Generated:** {now}  ",
        f"**Commit:** {commit_hash}  ",
        f"**Status:** {'❌ BLOCKING DRIFT' if has_unapproved_drifts else '✅ PASSED (or Approved Drifts)'}",
        ""
    ]
    
    if drift_msgs:
        md.append("## 🚨 Governance Drifts")
        for msg in drift_msgs:
            md.append("```text")
            md.append(msg.strip())
            md.append("```")
            md.append("")
        
    md.append("## Coverage Matrix")
    for rid in sorted(rules.keys()):
        data = rules[rid]
        impl_mark = "✔" if data["implementations"] else "✘ Missing"
        test_mark = "✔" if data["tests"] else "✘ Missing"
        md.append(f"### {rid}: {data['name']}")
        md.append(f"**Lifecycle:** {data['lifecycle']}")
        md.append(f"\n**Implementation:**")
        if data["implementations"]:
            for i in data["implementations"]:
                md.append(f"- {i}")
        else:
            md.append(f"- {impl_mark}")
            
        md.append(f"\n**Tests:**")
        if data["tests"]:
            for t in data["tests"]:
                md.append(f"- {t}")
        else:
            md.append(f"- {test_mark}")
        md.append("")
        
    with open(os.path.join(ROOT_DIR, 'traceability_report.md'), 'w') as f:
        f.write("\n".join(md))
        
    if drift_msgs:
        for msg in drift_msgs:
            print(msg)
            
    if has_unapproved_drifts:
        print("CI VALIDATION FAILED: Unapproved Governance Drifts block release.")
        exit(1)
    else:
        print("CI VALIDATION PASSED: No unapproved drifts found.")
        exit(0)

if __name__ == "__main__":
    import sys
    generate_reports()
