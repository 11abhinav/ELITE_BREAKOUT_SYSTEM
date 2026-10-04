import pandas as pd

df = pd.read_parquet('data/canonical_pit_rebuilt.parquet')
symbols = ['ARIS', 'BAYERCROP', 'BHARATWIRE', 'CUPID', 'DPABHUSHAN', 'GUJTHEM', 'HUHTAMAKI', 'JUSTDIAL', 'KIRIINDUS', 'KSL', 'MOIL', 'PATANJALI', 'SIGNATURE', 'GUJGASLTD', 'FRONTSP', 'GOCLCORP']
cols = ['symbol', 'cmp', 'shares_outstanding_m', 'market_cap', 'cash_and_equivalents', 'total_debt', 'enterprise_value', 'ebitda', 'current_ev_ebitda', 'provenance_status']
sub = df[df['symbol'].isin(symbols)][cols]
print(sub.to_string())
