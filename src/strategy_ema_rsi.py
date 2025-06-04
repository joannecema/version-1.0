# strategy_ema_rsi.py
import logging
import numpy as np
import talib
from typing import Optional, Tuple

log = logging.getLogger("EMARSI")

class EmaRsiStrategy:
    def __init__(self, api, config, tracker, executor):
        self.api = api
        self.config = config
        self.tracker = tracker
        self.executor = executor
        self.price_scale = {}
        
        self.timeframe = config.get("timeframe", "1m")
        self.lookback = max(50, config.get("lookback", 50))
        self.ema_short_period = config.get("ema_short", 12)
        self.ema_long_period = config.get("ema_long", 26)
        self.rsi_period = config.get("rsi_period", 14)
        self.rsi_oversold = config.get("rsi_oversold", 30)
        self.rsi_overbought = config.get("rsi_overbought", 70)
        self.strategy_name = "ema_rsi"
        self.testnet = config.get("testnet", False)

    async def _get_price_scale(self, symbol):
        if symbol not in self.price_scale:
            market = await self.api.load_market(symbol)
            self.price_scale[symbol] = market['precision']['price']
        return self.price_scale[symbol]

    async def check_signal(self, symbol: str) -> Optional[Tuple[str, float]]:
        try:
            if await self.tracker.has_open_position(symbol):
                return None
                
            # Get OHLCV with buffer for technical indicators
            ohlcv = await self.api.fetch_ohlcv_with_retry(
                symbol, 
                self.timeframe, 
                limit=self.lookback + self.ema_long_period + 5,
                max_retries=3
            )
            
            if len(ohlcv) < self.lookback:
                return None

            closes = np.array([bar[4] for bar in ohlcv], dtype=float)
            
            # Calculate indicators with TA-Lib
            ema_short = talib.EMA(closes, timeperiod=self.ema_short_period)[-1]
            ema_long = talib.EMA(closes, timeperiod=self.ema_long_period)[-1]
            rsi = talib.RSI(closes, timeperiod=self.rsi_period)[-1]
            
            if np.isnan(ema_short) or np.isnan(ema_long) or np.isnan(rsi):
                return None

            current_price = closes[-1]
            price_scale = await self._get_price_scale(symbol)
            size = await self.executor.calculate_risk_adjusted_size(
                symbol, 
                current_price,
                price_scale
            )

            # Signal conditions
            if ema_short > ema_long and rsi < self.rsi_oversold:
                log.info("[%s] BUY %s | EMA: %.4f > %.4f, RSI: %.2f", 
                         self.strategy_name.upper(), symbol, ema_short, ema_long, rsi)
                return ("buy", size)
            elif ema_short < ema_long and rsi > self.rsi_overbought:
                log.info("[%s] SELL %s | EMA: %.4f < %.4f, RSI: %.2f", 
                         self.strategy_name.upper(), symbol, ema_short, ema_long, rsi)
                return ("sell", size)
                
        except self.api.RateLimitExceeded:
            log.warning("[%s] Rate limit exceeded for %s", self.strategy_name.upper(), symbol)
            await asyncio.sleep(10)
        except Exception as e:
            log.exception("[%s] Error for %s: %s", self.strategy_name.upper(), symbol, e)
        return None
