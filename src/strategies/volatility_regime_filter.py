from src.utils import calculate_atr

class VolatilityRegimeFilter:
    def __init__(self, api, cfg):
        self.api, self.cfg = api, cfg

    async def allow_trading(self, symbol):
        ohlcv = await self.api.watch_ohlcv(symbol,self.cfg["timeframe"],self.cfg["lookback"]+1)
        atr=calculate_atr(ohlcv,self.cfg["atr_period"])
        return atr<=self.cfg["volatility_threshold_atr"]
