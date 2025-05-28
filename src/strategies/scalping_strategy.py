import logging
from src.utils import calculate_sma

class ScalpingStrategy:
    def __init__(self, api, tracker, executor, cfg):
        self.api = api
        self.tracker = tracker
        self.executor = executor
        self.cfg = cfg

    async def check_and_trade(self, symbol):
        tf = self.cfg["timeframe"]
        lb = self.cfg["lookback"]
        # get OHLCV for last lb+1 bars
        ohlcv = await self.api.get_ohlcv(symbol, tf, lb + 1)
        # compute moving averages
        closes = [bar[4] for bar in ohlcv]
        sma_short = calculate_sma(closes, self.cfg["sma_short"])
        sma_long  = calculate_sma(closes, self.cfg["sma_long"])
        price = closes[-1]

        if sma_short > sma_long and not self.tracker.has_position(symbol):
            logging.info(f"[SCALP] Entry {symbol} at {price}")
            await self.executor.enter_long(symbol, price)
        elif sma_short < sma_long and self.tracker.has_long(symbol):
            logging.info(f"[SCALP] Exit {symbol} at {price}")
            await self.executor.exit_position(symbol, price)
