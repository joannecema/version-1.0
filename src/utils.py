import math

def calculate_sma(values, period):
    """
    Simple Moving Average over `period` bars.
    `values` is a list of floats; must be at least `period` long.
    Returns the SMA for the last value.
    """
    if len(values) < period:
        raise ValueError(f"Not enough data for SMA (have {len(values)}, need {period})")
    return sum(values[-period:]) / period

def calculate_atr(ohlcv, period):
    """
    Average True Range.
    ohlcv: list of [ts, open, high, low, close, vol]
    period: number of bars for ATR
    Returns ATR for the last bar.
    """
    if len(ohlcv) < period + 1:
        raise ValueError(f"Not enough data for ATR (have {len(ohlcv)}, need {period+1})")
    trs = []
    for i in range(1, len(ohlcv)):
        _, _, high, low, close_prev, _ = (*[None]*2, *ohlcv[i][2:5], ohlcv[i-1][4], None)
        high_i = ohlcv[i][2]
        low_i  = ohlcv[i][3]
        tr = max(
            high_i - low_i,
            abs(high_i - close_prev),
            abs(low_i  - close_prev)
        )
        trs.append(tr)
    # first ATR is simple average of first `period` TRs
    atr = sum(trs[:period]) / period
    # subsequent ATRs use Wilder's smoothing
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr

def calculate_spread_zscore(ohlcv_a, ohlcv_b):
    """
    Z-score of the price spread between two symbols over the window.
    Uses closing prices only.
    """
    if len(ohlcv_a) != len(ohlcv_b):
        raise ValueError("OHLCV length mismatch for spread z-score")
    closes_a = [bar[4] for bar in ohlcv_a]
    closes_b = [bar[4] for bar in ohlcv_b]
    spread = [a - b for a, b in zip(closes_a, closes_b)]
    mean = sum(spread) / len(spread)
    variance = sum((s - mean) ** 2 for s in spread) / len(spread)
    stdev = math.sqrt(variance)
    if stdev == 0:
        return 0.0
    # z-score of the most recent spread
    return (spread[-1] - mean) / stdev
