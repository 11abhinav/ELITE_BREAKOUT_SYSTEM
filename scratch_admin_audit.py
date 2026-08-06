import os
import re

def audit_directory(dir_path):
    for root, _, files in os.walk(dir_path):
        for file in files:
            if not file.endswith('.py'):
                continue
            filepath = os.path.join(root, file)
            try:
                with open(filepath, 'r') as f:
                    content = f.read()
            except Exception:
                continue

            # Rule 4: save_alert_if_new(..., regime_ctx=...)
            if re.search(r'save_alert_if_new\([^)]*regime_ctx\s*=', content):
                print(f"[Rule 4 Violation] {filepath} passes regime_ctx directly to save_alert_if_new")

            # Rule 5: 2nd positional argument is dedup_key
            if re.search(r'save_alert_if_new\s*\([^,]+,\s*[^,]*dedup_key', content):
                print(f"[Rule 5 Violation] {filepath} passes dedup_key as 2nd arg to save_alert_if_new")

            # Rule 7: _MODE_CONFIG[...][4]
            if re.search(r'_MODE_CONFIG(?:\[.*?\])?\[4\]', content) or re.search(r'_MODE_CONFIG\.get[^)]*\)\[4\]', content):
                print(f"[Rule 7 Violation] {filepath} accesses _MODE_CONFIG at index 4")
                
            # Rule 11: _compute_target_quality kwargs
            if re.search(r'_compute_target_quality\s*\([^)]*(entry\s*=|atr_pct\s*=|target_1\s*=|support_score\s*=)', content):
                print(f"[Rule 11 Violation] {filepath} calls _compute_target_quality with kwargs")

            # Rule 25: fetch_delivery_data without skip_db_save
            if 'fetch_delivery_data' in content:
                for line in content.splitlines():
                    if 'fetch_delivery_data' in line and 'skip_db_save' not in line and 'def fetch_delivery_data' not in line:
                         print(f"[Rule 25 Warning] {filepath} calls fetch_delivery_data without skip_db_save (Line: {line.strip()})")

if __name__ == "__main__":
    audit_directory("/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app")
