import sys
sys.path.append('app')
from multibagger import calculate_fair_value, StockFundamentals, StockPriceData

f = StockFundamentals()
f.symbol="ICICIAMC.NS"
f.sector="Finance"
f.industry="Finance"
f.market_cap=50000
f.pe=49.19
f.pb=38.78
f.eps=66.54
f.bvps=84.39
f.roe=0.40
f.revenue_growth=0.20
f.tt_indpe=30.0
f.tt_indpb=2.0

p = StockPriceData()
p.symbol="ICICIAMC.NS"
p.price=3346.0
p.sma_50=3200
p.sma_200=3000
p.high_52w=3500
p.low_52w=2000
p.momentum_1m=0.1
p.momentum_3m=0.2
p.momentum_6m=0.3
p.momentum_1y=0.4
p.volume_avg_10d=200000
p.is_halted=False
p.latest_volume=200000

res = calculate_fair_value(f, p, {})
print(f"Fair Value: {res.fair_value}")
