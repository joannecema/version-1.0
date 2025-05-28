import numpy as np

def get_sma(arr):
    return float(np.mean(arr)) if arr else 0.0

def calculate_atr(ohlcv, period):
    trs = []
    for i in range(1, len(ohlcv)):
        h, l, pc = ohlcv[i][2], ohlcv[i][3], ohlcv[i-1][4]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return float(np.mean(trs[-period:])) if len(trs) >= period else 0.0

def orderbook_imbalance(book, levels):
    bid_vol = sum(b[1] for b in book["bids"][:levels])
    ask_vol = sum(a[1] for a in book["asks"][:levels])
    return (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-12)
