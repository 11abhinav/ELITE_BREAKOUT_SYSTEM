import re

with open("app/sl_target_helper.py", "r") as f:
    code = f.read()

# EOD
eod_t1_cluster_str = r""""sl_result": \{"target_candidate_pool": pool, "t1_source": t1_src\}"""
eod_replace = """t1_cluster = targets.get("t1_cluster")
    explanation = t1_cluster.analysis.explanation if t1_cluster and t1_cluster.analysis else {}
    "sl_result": {"target_candidate_pool": pool, "t1_source": t1_src, "explanation": explanation}"""
code = code.replace(eod_t1_cluster_str, eod_replace)

# MULTI_TF
multi_t1_cluster_str = r""""sl_result": \{"target_candidate_pool": pool, "t1_source": t1_src\}"""
code = code.replace(multi_t1_cluster_str, eod_replace)

# REVERSAL
rev_t1_cluster_str = r""""sl_result": \{"target_candidate_pool": \[\{\*\*vars\(c\), "source": c\.source\.name\} for c in cands\]\}"""
rev_replace = """t1_cluster = clusters[0] if clusters else None
    explanation = t1_cluster.analysis.explanation if t1_cluster and t1_cluster.analysis else {}
    "sl_result": {"target_candidate_pool": [{**vars(c), "source": c.source.name} for c in cands], "explanation": explanation}"""
code = re.sub(rev_t1_cluster_str, rev_replace, code)

with open("app/sl_target_helper.py", "w") as f:
    f.write(code)
print("Added explanation to payloads")
