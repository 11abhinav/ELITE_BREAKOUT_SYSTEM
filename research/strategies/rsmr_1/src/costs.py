"""
Indian Equity Statutory & Brokerage Cost Model
Calculates exact execution costs for delivery trading on NSE.
"""

from dataclasses import dataclass

@dataclass
class CostBreakdown:
    brokerage: float
    stt: float
    exchange_charges: float
    sebi_charges: float
    stamp_duty: float
    gst: float
    dp_charges: float
    slippage: float
    total_cost: float

def calculate_trade_costs(
    side: str,              # 'BUY' or 'SELL'
    shares: int,
    price: float,
    slippage_bps: float = 5.0,
    brokerage_rate: float = 0.0003, # 0.03% capped at ₹20
    brokerage_cap: float = 20.0
) -> CostBreakdown:
    if shares <= 0 or price <= 0:
        return CostBreakdown(0, 0, 0, 0, 0, 0, 0, 0, 0)

    trade_value = shares * price
    side = side.upper()

    # 1. Brokerage (0.03% or ₹20 max per executed order)
    brokerage = min(trade_value * brokerage_rate, brokerage_cap)

    # 2. STT (Securities Transaction Tax) - 0.1% on delivery SELL only
    stt = (trade_value * 0.001) if side == 'SELL' else 0.0

    # 3. Exchange turnover charges (0.00345% on NSE)
    exchange_charges = trade_value * 0.0000345

    # 4. SEBI turnover charges (₹10 per crore = 0.0001%)
    sebi_charges = trade_value * 0.000001

    # 5. Stamp duty (0.015% on delivery BUY only)
    stamp_duty = (trade_value * 0.00015) if side == 'BUY' else 0.0

    # 6. GST (18% on brokerage + exchange_charges + sebi_charges)
    gst = (brokerage + exchange_charges + sebi_charges) * 0.18

    # 7. Depository (DP) charges (₹13.50 + 18% GST = ₹15.93 per sell ISIN)
    dp_charges = 15.93 if side == 'SELL' else 0.0

    # 8. Modeled Slippage (in basis points)
    slippage = trade_value * (slippage_bps / 10000.0)

    total_cost = (
        brokerage + stt + exchange_charges + sebi_charges +
        stamp_duty + gst + dp_charges + slippage
    )

    return CostBreakdown(
        brokerage=round(brokerage, 2),
        stt=round(stt, 2),
        exchange_charges=round(exchange_charges, 2),
        sebi_charges=round(sebi_charges, 2),
        stamp_duty=round(stamp_duty, 2),
        gst=round(gst, 2),
        dp_charges=round(dp_charges, 2),
        slippage=round(slippage, 2),
        total_cost=round(total_cost, 2)
    )
