import math

def calculate_sma(values, period):
    if len(values) < period:
        raise ValueError(f"Not enough data for SMA (have {len(values)}, need {period})")
    return sum(values[-period:]) / period

def calculate_atr(ohlcv, period):
    if len(ohlcv) < period + 1:
        raise ValueError(f"Not enough data for ATR (have {len(ohlcv)}, need {period+1})")
    trs = []
    for i in range(1, len(ohlcv)):
        high, low = ohlcv[i][2], ohlcv[i][3]
        close_prev = ohlcv[i-1][4]
        tr = max(
            high - low,
            abs(high - close_prev),
            abs(low  - close_prev)
        )
        trs.append(tr)
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr

def calculate_spread_zscore(ohlcv_a, ohlcv_b):
    if len(ohlcv_a) != len(ohlcv_b):
        raise ValueError("OHLCV length mismatch for z-score")
    closes_a = [bar[4] for bar in ohlcv_a]
    closes_b = [bar[4] for bar in ohlcv_b]
    spread = [a - b for a, b in zip(closes_a, closes_b)]
    mean = sum(spread) / len(spread)
    var  = sum((s - mean)**2 for s in spread) / len(spread)
    stdev = math.sqrt(var)
    return 0.0 if stdev == 0 else (spread[-1] - mean) / stdev

def calculate_order_book_imbalance(order_book, levels):
    bids = order_book.get("bids", [])[:levels]
    asks = order_book.get("asks", [])[:levels]
    bid_vol = sum(v for _, v in bids)
    ask_vol = sum(v for _, v in asks)
    total = bid_vol + ask_vol + 1e-8
    return (bid_vol - ask_vol) / total
