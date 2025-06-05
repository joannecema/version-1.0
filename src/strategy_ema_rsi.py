# strategy_ema_rsi.py
import logging
import numpy as np
import talib
import asyncio
from typing import Optional, Tuple

log = logging.getLogger("EMARSI")

class EmaRsiStrategy:
    def __init__(self, api, config, tracker, executor):
        self.api = api
        self.config = config
        self.tracker = tracker
        self.executor = executor
        self.price_scale = {}
        self.strategy_name = "ema_rsi"

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
                limit=50 + 5
            )
            
            if len(ohlcv) < 50:
                return None

            closes = np.array([bar[4] for bar in ohlcv], dtype=float)
            ema_short = talib.EMA(closes, timeperiod=self.config.get("ema_short", 12))[-1]
            ema_long = talib.EMA(closes, timeperiod=self.config.get("ema_long", 26))[-1]
            rsi = talib.RSI(closes, timeperiod=self.config.get("rsi_period", 14))[-1]
            
            if np.isnan(ema_short) or np.isnan(ema_long) or np.isnan(rsi):
                return None

            current_price = closes[-1]
            price_scale = await self._get_price_scale(symbol)
            size = await self.executor.calculate_risk_adjusted_size(
                symbol, 
                current_price,
                price_scale
            )

            if ema_short > ema_long and rsi < self.config.get("rsi_oversold", 30):
                log.info("[%s] BUY %s | EMA: %.4f > %.4f, RSI: %.2f", 
                         self.strategy_name.upper(), symbol, ema_short, ema_long, rsi)
                return ("buy", size)
            elif ema_short < ema_long and rsi > self.config.get("rsi_overbought", 70):
                log.info("[%s] SELL %s | EMA: %.4f < %.4f, RSI: %.2f", 
                         self.strategy_name.upper(), symbol, ema_short, ema_long, rsi)
                return ("sell", size)
                
        except Exception as e:
            log.exception("[%s] Error for %s: %s", self.strategy_name.upper(), symbol, e)
        return None
