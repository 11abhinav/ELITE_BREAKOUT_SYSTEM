import re

with open("app/sl_target_helper.py", "r") as f:
    code = f.read()

reversal_pool = r""""sl_result": \{"target_candidate_pool": \[vars\(c\) for c in cands\]\}"""
reversal_replace = """"sl_result": {"target_candidate_pool": [{**vars(c), "source": c.source.name} for c in cands]}"""
code = re.sub(reversal_pool, reversal_replace, code)

with open("app/sl_target_helper.py", "w") as f:
    f.write(code)
print("Fixed pool json serialization bug")
