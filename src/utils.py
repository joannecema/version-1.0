import math
import logging

log = logging.getLogger("Utils")

def calculate_sma(values, period):
    if not values or len(values) < period:
        log.warning(f"[SMA] Not enough data (have {len(values)}, need {period})")
        return None
    return sum(values[-period:]) / period

def calculate_atr(ohlcv, period):
    if not ohlcv or len(ohlcv) < period + 1:
        log.warning(f"[ATR] Not enough data (have {len(ohlcv)}, need {period+1})")
        return None

    trs = []
    try:
        for i in range(1, len(ohlcv)):
            high, low = ohlcv[i][2], ohlcv[i][3]
            close_prev = ohlcv[i - 1][4]
            tr = max(
                high - low,
                abs(high - close_prev),
                abs(low - close_prev)
            )
            trs.append(tr)

        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr
    except Exception as e:
        log.error(f"[ATR] Error during calculation: {e}")
        return None

def calculate_spread_zscore(ohlcv_a, ohlcv_b):
    if not ohlcv_a or not ohlcv_b:
        log.warning("[Z-SCORE] One or both OHLCV series are empty")
        return None
    if len(ohlcv_a) != len(ohlcv_b):
        log.warning(f"[Z-SCORE] OHLCV length mismatch (A={len(ohlcv_a)}, B={len(ohlcv_b)})")
        return None

    try:
        closes_a = [bar[4] for bar in ohlcv_a]
        closes_b = [bar[4] for bar in ohlcv_b]
        spread = [a - b for a, b in zip(closes_a, closes_b)]
        mean = sum(spread) / len(spread)
        var = sum((s - mean) ** 2 for s in spread) / len(spread)
        stdev = math.sqrt(var)
        return 0.0 if stdev == 0 else (spread[-1] - mean) / stdev
    except Exception as e:
        log.error(f"[Z-SCORE] Error during calculation: {e}")
        return None

def calculate_order_book_imbalance(order_book, levels):
    try:
        bids = order_book.get("bids", [])[:levels]
        asks = order_book.get("asks", [])[:levels]
        bid_vol = sum(v for _, v in bids)
        ask_vol = sum(v for _, v in asks)
        total = bid_vol + ask_vol + 1e-8
        return (bid_vol - ask_vol) / total
    except Exception as e:
        log.error(f"[IMBALANCE] Error calculating order book imbalance: {e}")
        return 0.0