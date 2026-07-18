import sys

with open("app/data_quality.py", "r") as f:
    content = f.read()

old_score = """    @classmethod
    def _compute_score(cls, rows: int, expected: int, missing_pct: float, dupes: int, monotonic: bool, invalid_prices: int, freshness_days: int, now_dt: datetime) -> float:
        w = QUALITY_SCORE_WEIGHTS
        score = 0.0
        
        # Row Completeness
        completeness = min(1.0, rows / expected) if expected > 0 else 1.0
        score += w["row_completeness"] * completeness
        
        # Missing
        missing_penalty = missing_pct * 2.0
        score += max(0, w["missing"] - missing_penalty)
        
        # Price Sanity
        price_penalty = (invalid_prices / rows) * 100 if rows > 0 else w["price_sanity"]
        score += max(0, w["price_sanity"] - price_penalty)
        
        # Continuity
        cont_score = w["continuity"]
        if not monotonic: cont_score -= 5
        if dupes > 0: cont_score -= 5
        score += max(0, cont_score)
        
        # Freshness
        fresh_score = w["freshness"]
        if freshness_days > 0:
            is_weekend = now_dt.weekday() >= 5
            market_close = now_dt.replace(hour=15, minute=30, second=0, microsecond=0)
            
            if not is_weekend and now_dt > market_close and freshness_days >= 1:
                fresh_score -= 5 * freshness_days
            elif is_weekend and freshness_days > 2:
                fresh_score -= 5 * (freshness_days - 2)
                
        score += max(0, fresh_score)
        
        return max(0.0, min(100.0, score))"""

new_score = """    @classmethod
    def _compute_score(cls, rows: int, expected: int, missing_pct: float, dupes: int, monotonic: bool, invalid_prices: int, freshness_days: int, now_dt: datetime) -> float:
        w = QUALITY_SCORE_WEIGHTS
        score = 0.0
        
        # Row Completeness
        completeness = min(1.0, rows / expected) if expected > 0 else 1.0
        score += w["row_completeness"] * completeness
        
        # Missing
        missing_penalty = missing_pct * 2.0
        score += max(0, w["missing"] - missing_penalty)
        
        # Price Sanity
        price_penalty = (invalid_prices / rows) * 100 if rows > 0 else w["price_sanity"]
        score += max(0, w["price_sanity"] - price_penalty)
        
        # Continuity
        cont_score = w["continuity"]
        if not monotonic: cont_score -= 5
        if dupes > 0: cont_score -= 5
        score += max(0, cont_score)
        
        # Freshness
        fresh_score = w["freshness"]
        if freshness_days > 0:
            is_weekend = now_dt.weekday() >= 5
            market_close = now_dt.replace(hour=15, minute=30, second=0, microsecond=0)
            
            if not is_weekend and now_dt > market_close and freshness_days >= 1:
                fresh_score -= 5 * freshness_days
            elif is_weekend and freshness_days > 2:
                fresh_score -= 5 * (freshness_days - 2)
                
        # Heavy penalty if freshness is extreme (e.g. > 100 days)
        if freshness_days > 100:
            fresh_score -= 20
                
        score += max(0, fresh_score)
        
        # If completeness is severely lacking, apply a global scale down
        # A 1-row dataframe for a 1y request should not pass based on its 1 perfect row.
        if completeness < 0.10:
            score = score * completeness * 2.0  # Scales it down massively
            
        return max(0.0, min(100.0, score))"""

content = content.replace(old_score, new_score)

with open("app/data_quality.py", "w") as f:
    f.write(content)
