import logging
from src.utils import calculate_atr

class VolatilityRegimeFilter:
    def __init__(self, api, cfg):
        self.api = api
        self.cfg = cfg

    async def allow_trading(self, symbol):
        """
        Fetches OHLCV robustly via api.get_ohlcv().
        Returns False if data fetch fails or ATR > threshold.
        """
        try:
            ohlcv = await self.api.get_ohlcv(
                symbol,
                self.cfg["timeframe"],
                self.cfg["lookback"] + 1
            )
        except Exception as e:
            logging.error(f"[VRF] OHLCV fetch failed for {symbol}: {e}")
            return False

        atr = calculate_atr(ohlcv, self.cfg["atr_period"])
        allowed = atr <= self.cfg["volatility_threshold_atr"]
        logging.debug(f"[VRF] ATR={atr:.6f} threshold={self.cfg['volatility_threshold_atr']} allowed={allowed}")
        return allowed
