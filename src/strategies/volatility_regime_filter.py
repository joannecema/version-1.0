import logging
from src.utils import calculate_atr

class VolatilityRegimeFilter:
    def __init__(self, api, cfg):
        self.api = api
        self.cfg = cfg

    async def allow_trading(self, symbol):
        try:
            ohlcv = await self.api.watch_ohlcv(
                symbol,
                self.cfg["timeframe"],
                self.cfg["lookback"] + 1
            )
        except Exception as e:
            logging.warning(f"[VRF] failed to fetch OHLCV for {symbol}: {e}")
            return False
        atr = calculate_atr(ohlcv, self.cfg["atr_period"])
        return atr <= self.cfg["volatility_threshold_atr"]
