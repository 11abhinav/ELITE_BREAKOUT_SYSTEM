with open("app/sl_target_helper.py", "r") as f:
    content = f.read()

import re
# Find _compute_multi_tf function and replace rr_ratio with natural_rr
def repl(m):
    return m.group(0).replace('"rr_ratio":', '"natural_rr":')

content = re.sub(r'def _compute_multi_tf\(.*?return \{.*?\}', repl, content, flags=re.DOTALL)

with open("app/sl_target_helper.py", "w") as f:
    f.write(content)
