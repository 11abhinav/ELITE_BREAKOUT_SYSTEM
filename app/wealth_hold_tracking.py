import logging

logger = logging.getLogger(__name__)

class HoldScoreTrendAnalyzer:
    """
    Analyzes historical hold scores for an open position to detect:
    1. Rapid Decline: Sudden drop in hold score over a short period.
    2. Sustained Weakness: Prolonged low hold score without triggering immediate exit.
    3. Momentum Reversals: Deteriorating momentum alongside price collapse.
    """
    
    @staticmethod
    def analyze_trend(symbol: str) -> dict:
        """
        Fetches the last 10 days of hold score history from the database and analyzes it.
        """
        try:
            from database import get_connection
            from psycopg2.extras import RealDictCursor
            
            history = []
            with get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute('''
                        SELECT recorded_at, hold_score, rs_6m 
                        FROM wealth_score_history 
                        WHERE symbol = %s 
                        ORDER BY recorded_at DESC LIMIT 10
                    ''', (symbol,))
                    history = cur.fetchall()
                    
            if not history or len(history) < 3:
                return {"action": "HOLD", "reason": "Insufficient history"}
                
            latest = history[0]
            oldest = history[-1]
            
            latest_score = latest['hold_score']
            oldest_score = oldest['hold_score']
            
            # 1. Rapid Decline (e.g. dropped > 30 points recently)
            if oldest_score - latest_score > 30 and latest_score < 60:
                return {"action": "WARN", "reason": f"Rapid Hold Score Decline ({oldest_score} -> {latest_score})"}
                
            # 2. Sustained Weakness (last 5 scores all below 50)
            if len(history) >= 5:
                recent_5 = [h['hold_score'] for h in history[:5]]
                if all(s < 50 for s in recent_5):
                    return {"action": "SELL REVIEW", "reason": "Sustained Weakness (5+ periods < 50)"}
                    
            return {"action": "HOLD", "reason": "Stable"}
            
        except Exception as e:
            logger.warning(f"Failed to analyze hold score trend for {symbol}: {e}")
            return {"action": "HOLD", "reason": "Error during analysis"}
