import logging
from src.utils import calculate_sma

class ScalpingStrategy:
    def __init__(self, api, tracker, executor, cfg):
        self.api = api
        self.tracker = tracker
        self.executor = executor
        self.cfg = cfg
        self.logger = logging.getLogger("ScalpingStrategy")

    async def check_and_trade(self, symbol):
        try:
            tf = self.cfg["timeframe"]
            lb = self.cfg["lookback"]

            ohlcv = await self.api.get_ohlcv(symbol, tf, lb + 1)
            if len(ohlcv) < lb + 1:
                self.logger.warning(f"[SCALP] Not enough OHLCV data for {symbol} (have {len(ohlcv)}, need {lb + 1})")
                return

            closes = [bar[4] for bar in ohlcv]
            sma_short = calculate_sma(closes, self.cfg["sma_short"])
            sma_long  = calculate_sma(closes, self.cfg["sma_long"])
            price = closes[-1]

            if sma_short is None or sma_long is None:
                self.logger.warning(f"[SCALP] SMA calculation failed for {symbol}")
                return

            if sma_short > sma_long and not self.tracker.has_position(symbol):
                self.logger.info(f"[SCALP] Entry {symbol} @ {price}")
                await self.executor.enter_long(symbol, price)

            elif sma_short < sma_long:
                if hasattr(self.tracker, "has_long") and self.tracker.has_long(symbol):
                    self.logger.info(f"[SCALP] Exit {symbol} @ {price}")
                    await self.executor.exit_position(symbol, price)
                else:
                    self.logger.debug(f"[SCALP] No long position to exit for {symbol}")

        except Exception as e:
            self.logger.error(f"[SCALP] Error on {symbol}: {e}")