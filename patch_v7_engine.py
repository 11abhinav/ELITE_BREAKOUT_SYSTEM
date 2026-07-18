import re

with open('app/sl_target_helper.py', 'r') as f:
    content = f.read()

# 1. Deduplicate CandidateGenerator
cand_gen_pattern = re.compile(r'(        for c in candidates:\n            c\.score = TargetScorer\.score\(c, macro_regime\)\n        return candidates)')

cand_gen_repl = """        unique_cands = {}
        for c in candidates:
            key = (c.source.name, round(c.price, 2))
            if key not in unique_cands:
                unique_cands[key] = c
        
        candidates = list(unique_cands.values())
        
        for c in candidates:
            c.score = TargetScorer.score(c, macro_regime)
        return candidates"""

content = cand_gen_pattern.sub(cand_gen_repl, content)

# 2. Tie break determinism in ConflictResolver
resolve_pattern = re.compile(r'class ConflictResolver:.*?return clusters', re.DOTALL)

resolve_repl = """class ConflictResolver:
    @staticmethod
    def resolve(clusters: List[ClusteredTarget], scanner: str, entry: float, macro_regime: str) -> List[ClusteredTarget]:
        policy = TARGET_CONFLICT_POLICY.get(scanner, "CONFIDENCE")
        if policy == "NEAREST":
            return sorted(clusters, key=lambda c: (c.consensus_price, -c.score, c.cluster_id))
        elif policy == "CONFIDENCE":
            return sorted(clusters, key=lambda c: (c.score, c.consensus_price, -c.cluster_id), reverse=True)
        elif policy == "REGIME":
            if macro_regime in ("BULL", "TRENDING"):
                return sorted(clusters, key=lambda c: (c.consensus_price, c.score, -c.cluster_id), reverse=True) # Prefer higher
            else:
                return sorted(clusters, key=lambda c: (c.score, c.consensus_price, -c.cluster_id), reverse=True)
        return sorted(clusters, key=lambda c: (c.score, c.consensus_price, -c.cluster_id), reverse=True)"""

content = resolve_pattern.sub(resolve_repl, content)

# 3. ClusterEngine boundary condition test: ensure it includes distance <= window
# The code currently has: if cand.price - cluster_min <= window:
# This is correct. `101 - 100 <= 1.0` evaluates to True. We just need to make sure floating point rounding doesn't fail us.
cluster_pattern = re.compile(r'if cand\.price - cluster_min <= window:')
cluster_repl = 'if cand.price - cluster_min <= window + 1e-6:'
content = cluster_pattern.sub(cluster_repl, content)

with open('app/sl_target_helper.py', 'w') as f:
    f.write(content)
