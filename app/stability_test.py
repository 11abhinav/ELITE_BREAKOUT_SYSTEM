import os
import sys
import pandas as pd
from datetime import datetime

# Setup
os.environ["ELITE_MODE"] = "TEST"
from multibagger import fetch_ticker_fundamentals
from core_score_engine import PeerMetrics, CorePriceData, generate_core_scores
from valuation_utils import fetch_full_universe_for_valuation, compute_peer_medians

def run_stability_test():
    print("Running Stability Test for Nifty 500 & Smallcap 250 subset...")
    universe_df = fetch_full_universe_for_valuation()
    if universe_df is None or universe_df.empty:
        print("Universe is empty. Exiting.")
        return
        
    # Pick a random subset of 100 stocks for speed
    test_symbols = universe_df["ticker"].dropna().sample(n=100, random_state=42).tolist()
    
    print(f"Selected {len(test_symbols)} symbols. Pre-computing peer medians...")
    peer_medians = compute_peer_medians(test_symbols)
    
    results = []
    
    for i, sym in enumerate(test_symbols):
        if i % 10 == 0:
            print(f"Processed {i}/{len(test_symbols)}...")
            
        try:
            fund = fetch_ticker_fundamentals(sym)
            if not fund:
                continue
                
            p_data = peer_medians.get(sym, {})
            cp = PeerMetrics(
                median_pe=p_data.get("median_pe"),
                median_pb=p_data.get("median_pb"),
                median_roe=p_data.get("median_roe", 0) / 100.0 if p_data.get("median_roe") else None,
                median_ev_ebitda=p_data.get("median_ev_ebitda"),
                median_div_yield=p_data.get("median_div_yield", 0) / 100.0 if p_data.get("median_div_yield") else None,
                median_peg=p_data.get("median_peg"),
                peer_count=p_data.get("peer_count", 0),
                dispersion_iqr_median=p_data.get("dispersion_iqr_median"),
                source_type=p_data.get("source_type", "FALLBACK"),
                is_complete=(p_data.get("median_pe") is not None and p_data.get("median_pb") is not None),
                missing_critical=(p_data.get("median_pe") is None),
                missing_minor=False
            )
            
            c_price = CorePriceData(
                price=None, sma_50=None, sma_200=None, high_52w=None, high_20d=None,
                latest_volume=None, volume_sma20=None, rs_nifty=None, rs_sector=None, eps_revision_score=None
            )
            
            scores = generate_core_scores(fund, cp, c_price)
            results.append({
                "symbol": sym,
                "score": scores.overall_score,
                "rating": scores.institutional_rating,
                "q_cov": scores.quality.coverage,
                "warnings": len(scores.warnings)
            })
        except Exception as e:
            print(f"Error processing {sym}: {e}")
            
    df = pd.DataFrame(results)
    if not df.empty:
        print("\n=== STABILITY TEST RESULTS ===")
        print(f"Total Scored: {len(df)}")
        print("\nScore Distribution:")
        print(df['score'].describe())
        print("\nRating Distribution:")
        print(df['rating'].value_counts(normalize=True) * 100)
        
        print("\nWarnings Triggers (Kill-Gates):")
        print(df[df['warnings'] > 0]['symbol'].count())
        
        # Save sample for inspection
        df.to_csv("stability_test_results.csv", index=False)
        print("\nSaved full results to stability_test_results.csv")
        
if __name__ == "__main__":
    run_stability_test()
