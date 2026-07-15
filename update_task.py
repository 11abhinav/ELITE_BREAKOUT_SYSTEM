import re

with open('/Users/abhinavmaheshwari/.gemini/antigravity-ide/brain/a5ac76af-36ed-4b05-ab4c-abeb39d17aed/task.md', 'r') as f:
    content = f.read()

content = content.replace('`[/]` 1. **Data Parsing & Wealth Engine Core**', '`[x]` 1. **Data Parsing & Wealth Engine Core**')
content = content.replace('`[ ]` Patch `_safe_float` in `wealth_engine.py` to explicitly catch `""` empty strings.', '`[x]` Patch `_safe_float` in `wealth_engine.py` to explicitly catch `""` empty strings.')
content = content.replace('`[ ]` Replace all raw `float(val)` calls in `wealth_engine.py` with `_safe_float` / `_safe_num`.', '`[x]` Replace all raw `float(val)` calls in `wealth_engine.py` with `_safe_float` / `_safe_num`.')
content = content.replace('`[ ]` Fix `YOY Revenue %` / `ROE %` unsafe accesses in `reversal_scanner.py` and `eod_scanner.py`.', '`[x]` Fix `YOY Revenue %` / `ROE %` unsafe accesses in `reversal_scanner.py` and `eod_scanner.py`.')

content = content.replace('`[ ]` 2. **UI / UX Dashboard Fixes (Rule #25, #34)**', '`[x]` 2. **UI / UX Dashboard Fixes (Rule #25, #34)**')
content = content.replace('`[ ]` Replace `grid-template-columns: repeat(auto-fit...)` with Flexbox in `admin_dashboard.html`.', '`[x]` Replace `grid-template-columns: repeat(auto-fit...)` with Flexbox in `admin_dashboard.html`.')
content = content.replace('`[ ]` Replace `grid-template-columns: repeat(auto-fit...)` with Flexbox in `user_dashboard.html`.', '`[x]` Replace `grid-template-columns: repeat(auto-fit...)` with Flexbox in `user_dashboard.html`.')
content = content.replace('`[ ]` Replace `grid-template-columns: repeat(auto-fit...)` with Flexbox in `wealth_dashboard.html`.', '`[x]` Replace `grid-template-columns: repeat(auto-fit...)` with Flexbox in `wealth_dashboard.html`.')
content = content.replace('`[ ]` Verify Collapsible Panel logic for admin health/multibagger panels.', '`[x]` Verify Collapsible Panel logic for admin health/multibagger panels.')

content = content.replace('`[ ]` 3. **Fyers & Data Provider (Rule #39)**', '`[x]` 3. **Fyers & Data Provider (Rule #39)**')
content = content.replace('`[ ]` Implement Reverse Fallback in `data_provider.py` to auto-invalidate `.BO` mappings that fail and fallback to `.NS`.', '`[x]` Implement Reverse Fallback in `data_provider.py` to auto-invalidate `.BO` mappings that fail and fallback to `.NS`.')

content = content.replace('`[ ]` 4. **Database & State Integrity (Rule #36)**', '`[x]` 4. **Database & State Integrity (Rule #36)**')
content = content.replace('`[ ]` Review `sanitize` logic in `database.py` to ensure complete coverage for NaN/Inf/NA before JSON serialization.', '`[x]` Review `sanitize` logic in `database.py` to ensure complete coverage for NaN/Inf/NA before JSON serialization.')

content = content.replace('`[ ]` 5. **Verification**', '`[x]` 5. **Verification**')
content = content.replace('`[ ]` Run `pytest` test suite to verify no regressions.', '`[x]` Run `pytest` test suite to verify no regressions.')
content = content.replace('`[ ]` Commit and push changes.', '`[/]` Commit and push changes.')

with open('/Users/abhinavmaheshwari/.gemini/antigravity-ide/brain/a5ac76af-36ed-4b05-ab4c-abeb39d17aed/task.md', 'w') as f:
    f.write(content)
