"""
Audit Baseline Cohort
Extracts the baseline 46 incomplete stocks, 12 structural ineligible stocks, and 4 stale stocks
from data/v2_health_population_audit.csv and outputs a clean summary.
"""
import pandas as pd
import json

df = pd.read_csv('data/v2_health_population_audit.csv')

total = len(df)
structural_df = df[df['structural_ineligible'] == True]
stale_df = df[df['other_data_failure'] == True]
incomplete_df = df[(df['incomplete'] == True) & (df['structural_ineligible'] == False) & (df['other_data_failure'] == False)]
evaluable_df = df[df['fully_evaluable'] == True]

print(f"Total Universe: {total}")
print(f"Fully Evaluable: {len(evaluable_df)}")
print(f"Structural Ineligible: {len(structural_df)}: {sorted(structural_df['symbol'].tolist())}")
print(f"Stale: {len(stale_df)}: {sorted(stale_df['symbol'].tolist())}")
print(f"Incomplete: {len(incomplete_df)}")

# Breakdown of Incomplete by Reason
print("\nIncomplete breakdown:")
print(incomplete_df['data_failure_reasons'].value_counts())

results = {
    "total": total,
    "evaluable_count": len(evaluable_df),
    "structural_count": len(structural_df),
    "structural_symbols": sorted(structural_df['symbol'].tolist()),
    "stale_count": len(stale_df),
    "stale_symbols": sorted(stale_df['symbol'].tolist()),
    "incomplete_count": len(incomplete_df),
    "incomplete_symbols": sorted(incomplete_df['symbol'].tolist()),
    "incomplete_records": incomplete_df[['symbol', 'quality_data_failure', 'valuation_data_failure', 'price_data_failure', 'data_failure_reasons']].to_dict(orient='records')
}

with open('data/baseline_46_cohort.json', 'w') as f:
    json.dump(results, f, indent=2)

print("\nWrote data/baseline_46_cohort.json successfully.")
