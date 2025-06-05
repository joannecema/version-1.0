# strategy_breakout.py
import logging
import numpy as np
import asyncio
from typing import Optional, Tuple

log = logging.getLogger("Breakout")

class BreakoutStrategy:
    def __init__(self, api, config, tracker, executor):
        self.api = api
        self.config = config
        self.tracker = tracker
        self.executor = executor
        self.price_scale = {}
        self.strategy_name = "volume_breakout"
        self.min_contract_size = config.get("min_contract_size", 1)

    async def _get_price_scale(self, symbol):
        if symbol not in self.price_scale:
            market = await self.api.load_market(symbol)
            self.price_scale[symbol] = market['precision']['price']
        return self.price_scale[symbol]

    async def check_signal(self, symbol: str) -> Optional[Tuple[str, float]]:
        try:
            if await self.tracker.has_open_position(symbol):
                return None
                
            ohlcv = await self.api.get_ohlcv(
                symbol, 
                self.config.get("timeframe", "1m"), 
                limit=self.config.get("breakout_lookback", 20) + 1
            )
            
            if len(ohlcv) < self.config.get("breakout_lookback", 20) + 1:
                return None

            ohlcv_arr = np.array(ohlcv)
            highs = ohlcv_arr[:-1, 2].astype(float)
            lows = ohlcv_arr[:-1, 3].astype(float)
            volumes = ohlcv_arr[:-1, 5].astype(float)
            
            last_candle = ohlcv_arr[-1]
            current_high = float(last_candle[2])
            current_low = float(last_candle[3])
            current_volume = float(last_candle[5])
            current_close = float(last_candle[4])

            max_high = np.max(highs)
            min_low = np.min(lows)
            avg_volume = np.mean(volumes)

            if current_volume < avg_volume * self.config.get("volume_multiplier", 2.0):
                return None

            price_scale = await self._get_price_scale(symbol)
            size = await self.executor.calculate_risk_adjusted_size(
                symbol, 
                current_close,
                price_scale
            )
            if size < self.min_contract_size:
                return None

            if current_high > max_high:
                log.info("[%s] BREAKOUT UP %s | High: %.4f > %.4f", 
                         self.strategy_name.upper(), symbol, current_high, max_high)
                return ("buy", size)
            elif current_low < min_low:
                log.info("[%s] BREAKOUT DOWN %s | Low: %.4f < %.4f", 
                         self.strategy_name.upper(), symbol, current_low, min_low)
                return ("sell", size)
                
        except Exception as e:
            log.exception("[%s] Error for %s: %s", self.strategy_name.upper(), symbol, e)
        return None
