import re

with open('/Users/abhinavmaheshwari/.gemini/antigravity-ide/brain/a5ac76af-36ed-4b05-ab4c-abeb39d17aed/task.md', 'r') as f:
    content = f.read()

content = content.replace('`[/]` 1. **Timezone Bug Fixes (UTC -> IST)**', '`[x]` 1. **Timezone Bug Fixes (UTC -> IST)**')
content = content.replace('`[ ]` Patch `fyers_auth.py`', '`[x]` Patch `fyers_auth.py`')
content = content.replace('`[ ]` Patch `multibagger.py` to use IST timezone', '`[x]` Patch `multibagger.py` to use IST timezone')
content = content.replace('`[ ]` Patch `pledge_scraper.py`', '`[x]` Patch `pledge_scraper.py`')

content = content.replace('`[ ]` 2. **Defensive Type Casting in Multibagger**', '`[x]` 2. **Defensive Type Casting in Multibagger**')
content = content.replace('`[ ]` Patch `safe_float` in `multibagger.py`', '`[x]` Patch `safe_float` in `multibagger.py`')

content = content.replace('`[ ]` 3. **Indicator NaN Protections**', '`[x]` 3. **Indicator NaN Protections**')
content = content.replace('`[ ]` Patch `eod_scanner.py` to avoid unsafe', '`[x]` Patch `eod_scanner.py` to avoid unsafe')
content = content.replace('`[ ]` Patch `multi_tf_scanner.py` to avoid unsafe', '`[x]` Patch `multi_tf_scanner.py` to avoid unsafe')

content = content.replace('`[ ]` 4. **Verification**', '`[x]` 4. **Verification**')
content = content.replace('`[ ]` Run `pytest`', '`[x]` Run `pytest`')
content = content.replace('`[ ]` Commit and push changes.', '`[/]` Commit and push changes.')

with open('/Users/abhinavmaheshwari/.gemini/antigravity-ide/brain/a5ac76af-36ed-4b05-ab4c-abeb39d17aed/task.md', 'w') as f:
    f.write(content)
