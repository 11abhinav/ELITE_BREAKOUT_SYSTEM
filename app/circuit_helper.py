def is_valid_circuit_candle(candle_range: float, volume: float, close_price: float, min_stock_price: float = 10.0) -> bool:
    """
    Validates if a zero-range candle is a legitimate circuit block.
    A legitimate circuit has:
    1. Zero range (Open = High = Low = Close)
    2. Positive volume (people are queued/trading at the limit price)
    3. Price >= MIN_STOCK_PRICE (avoids penny stock garbage data)
    """
    if candle_range != 0:
        return False
    if volume <= 0:
        return False
    if close_price < min_stock_price:
        return False
    return True
