import os
import sys
sys.path.append(os.path.join(os.getcwd(), 'app'))

from core_score_engine import compute_relative_value_band, CoreFundamentals, PeerMetrics

f = CoreFundamentals(
    symbol="ICICIAMC",
    sector="Financials",
    pe=1,
    pb=1,
    roe=0.2,
    roce=0.2,
    debt_equity=0,
    operating_margin=0.2,
    revenue_growth_3y=0.1,
    revenue_growth_5y=0.1,
    revenue_growth_1y=0.1,
    eps_growth_3y=0.1,
    eps_growth_5y=0.1,
    eps_growth_1y=0.1,
    fcf_margin=0.1,
    cfo_pat_ratio=1,
    operating_cash_flow=1,
    yoy_profit_growth=0.1,
    net_losses_3y=False,
    div_yield=0.01,
    eps=50,
    bvps=50,
    roa=0.1,
    is_financial=True
)

p = PeerMetrics(
    median_pe=20,
    median_pb=5,
    median_roe=0.15,
    median_peg=1,
    peer_count=10,
    dispersion_iqr_median=0.1,
    source_type="REFINED",
    is_complete=True,
    missing_critical=False,
    missing_minor=False
)

reliability = 15.0
current_price = 3282.30

res = compute_relative_value_band(f, p, reliability, current_price)
print("FV =", res)
