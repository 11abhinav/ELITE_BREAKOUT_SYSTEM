from abc import ABC, abstractmethod

def interpolate(value, input_min, input_max, output_min, output_max):
    """
    Linearly maps a value from an input range to an output range, capped at min/max.
    """
    if value <= input_min: return output_min
    if value >= input_max: return output_max
    
    proportion = (value - input_min) / float(input_max - input_min)
    mapped = output_min + (proportion * (output_max - output_min))
    return int(mapped)

class StrategyPolicy(ABC):
    @abstractmethod
    def evaluate(self, context: dict) -> dict:
        pass

class BreakoutPolicy(StrategyPolicy):
    def evaluate(self, context: dict) -> dict:
        score = context.get("market_score", 50)
        conf = context.get("confidence", {}).get("score", 50)
        
        # Linear interpolation for bias: 20 -> -20, 80 -> +20
        breakout_bias = interpolate(score, 20, 80, -20, 20)
        
        # Risk scaled by both Market Score and Confidence
        # Base risk multiplier is interpolated 20 -> 0.3, 80 -> 1.0
        base_risk = interpolate(score, 20, 80, 30, 100) / 100.0
        
        # If signals strongly disagree (confidence < 40%), penalize risk
        risk_mult = base_risk
        if conf < 40:
            risk_mult *= 0.7
            
        max_pos = max(1, int(score / 20))
        max_risk = max(2, int(score / 15))

        return {
            "trade_bias": {
                "breakout": breakout_bias,
                "mean_reversion": 0
            },
            "risk": {
                "multiplier": round(risk_mult, 2),
                "max_new_positions": max_pos,
                "max_open_risk": max_risk
            }
        }

class ReversalPolicy(StrategyPolicy):
    def evaluate(self, context: dict) -> dict:
        score = context.get("market_score", 50)
        conf = context.get("confidence", {}).get("score", 50)
        phase = context.get("market_phase", "CONSOLIDATION")
        
        # Mean reversion interpolates oppositely for general score
        mr_bias = interpolate(score, 20, 80, 25, -25)
        
        # Override with phase-specific logic
        if phase in ["CONSOLIDATION", "CAPITULATION"]:
            mr_bias += 15
        elif phase == "EXPANSION":
            mr_bias -= 20
            
        mr_bias = max(-30, min(30, mr_bias))
        
        base_risk = 0.8
        if phase == "EXPANSION": base_risk = 0.4
        elif phase == "CONSOLIDATION": base_risk = 1.0
        
        risk_mult = base_risk
        if conf < 40:
            risk_mult *= 0.8

        return {
            "trade_bias": {
                "breakout": 0,
                "mean_reversion": mr_bias
            },
            "risk": {
                "multiplier": round(risk_mult, 2),
                "max_new_positions": 4,
                "max_open_risk": 5
            }
        }

class StrategyPolicyEngine:
    @staticmethod
    def get_policy(context: dict, strategy_type: str = "MULTI_TF") -> dict:
        if strategy_type in ["MULTI_TF", "EOD"]:
            policy = BreakoutPolicy()
        elif strategy_type == "REVERSAL":
            policy = ReversalPolicy()
        else:
            policy = BreakoutPolicy()
            
        return policy.evaluate(context)
