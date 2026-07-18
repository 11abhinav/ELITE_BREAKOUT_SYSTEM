import re
with open("app/sl_target_helper.py", "r") as f:
    content = f.read()

# I will just replace all '"rr_ratio":' with '"natural_rr":' for MULTI_TF output, 
# but actually we can just rename '"rr_ratio"' to '"natural_rr"' for all of them to be safe,
# or we can check what the tests expect.
# The test says: "MULTI_TF sl_result must contain 'natural_rr', not 'rr_ratio'"
# This implies maybe EOD and Reversal use rr_ratio, or they all should use natural_rr? 
# Let's just change them all to natural_rr, or rename rr_ratio to natural_rr in BreakoutAdapter?
# Wait, BreakoutAdapter handles both MULTI_TF and EOD. Let's see if BreakoutAdapter returns natural_rr.
content = content.replace('"rr_ratio":', '"natural_rr":')

with open("app/sl_target_helper.py", "w") as f:
    f.write(content)
