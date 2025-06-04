# strategy_breakout.py
import logging
import numpy as np
from typing import Optional, Tuple

log = logging.getLogger("Breakout")

class BreakoutStrategy:
    def __init__(self, api, config, tracker, executor):
        self.api = api
        self.config = config
        self.tracker = tracker
        self.executor = executor
        self.price_scale = {}  # Symbol -> price scaling factor
        
        self.timeframe = config.get("timeframe", "1m")
        self.lookback = int(config.get("breakout_lookback", 20))
        self.volume_multiplier = config.get("volume_multiplier", 2.0)
        self.min_roi = config.get("min_roi_threshold", 0.002)
        self.strategy_name = "volume_breakout"
        self.min_contract_size = config.get("min_contract_size", 1)
        self.testnet = config.get("testnet", False)

    async def _get_price_scale(self, symbol):
        if symbol not in self.price_scale:
            market = await self.api.load_market(symbol)
            self.price_scale[symbol] = market['precision']['price']
        return self.price_scale[symbol]

    async def check_signal(self, symbol: str) -> Optional[Tuple[str, float]]:
        try:
            # Check cooldown and position status first
            if await self.tracker.has_open_position(symbol):
                log.debug("[%s] Position exists for %s", self.strategy_name.upper(), symbol)
                return None
                
            # Fetch OHLCV with retry
            ohlcv = await self.api.fetch_ohlcv_with_retry(
                symbol, 
                self.timeframe, 
                limit=self.lookback + 1,
                max_retries=3
            )
            
            if len(ohlcv) < self.lookback + 1:
                log.warning("[%s] Insufficient data: %s (%d candles)", 
                           self.strategy_name.upper(), symbol, len(ohlcv))
                return None

            # Convert to numpy for vector operations
            ohlcv_arr = np.array(ohlcv)
            highs = ohlcv_arr[:-1, 2].astype(float)
            lows = ohlcv_arr[:-1, 3].astype(float)
            volumes = ohlcv_arr[:-1, 5].astype(float)
            
            last_candle = ohlcv_arr[-1]
            current_high = float(last_candle[2])
            current_low = float(last_candle[3])
            current_volume = float(last_candle[5])
            current_close = float(last_candle[4])

            # Vectorized calculations
            max_high = np.max(highs)
            min_low = np.min(lows)
            avg_volume = np.mean(volumes)

            # Volume filter
            if current_volume < avg_volume * self.volume_multiplier:
                log.debug("[%s] Volume filter: %s (%.2f < %.2f)", 
                         self.strategy_name.upper(), symbol, current_volume, avg_volume * self.volume_multiplier)
                return None

            # Position sizing with risk management
            price_scale = await self._get_price_scale(symbol)
            size = await self.executor.calculate_risk_adjusted_size(
                symbol, 
                current_close,
                price_scale
            )
            if size < self.min_contract_size:
                log.debug("[%s] Size too small: %s (%.4f)", 
                         self.strategy_name.upper(), symbol, size)
                return None

            # Breakout detection
            signal = None
            if current_high > max_high:
                log.info("[%s] BREAKOUT UP %s | High: %.4f > %.4f", 
                         self.strategy_name.upper(), symbol, current_high, max_high)
                signal = ("buy", size)
            elif current_low < min_low:
                log.info("[%s] BREAKOUT DOWN %s | Low: %.4f < %.4f", 
                         self.strategy_name.upper(), symbol, current_low, min_low)
                signal = ("sell", size)
                
            return signal

        except self.api.RateLimitExceeded:
            log.warning("[%s] Rate limit exceeded for %s", self.strategy_name.upper(), symbol)
            await asyncio.sleep(10)
        except self.api.ExchangeNotAvailable as e:
            log.error("[%s] Exchange issue: %s", self.strategy_name.upper(), e)
            await asyncio.sleep(30)
        except Exception as e:
            log.exception("[%s] Unexpected error for %s: %s", self.strategy_name.upper(), symbol, e)
        return None
