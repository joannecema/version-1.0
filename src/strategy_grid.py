# strategy_grid.py
import logging
import numpy as np
from typing import Optional, Tuple

log = logging.getLogger("GridStrategy")

class GridStrategy:
    def __init__(self, api, config, tracker, executor):
        self.api = api
        self.config = config
        self.tracker = tracker
        self.executor = executor
        self.price_scale = {}
        
        self.timeframe = config.get("timeframe", "1m")
        self.lookback = int(config.get("mm_lookback", 10))
        self.threshold = float(config.get("mm_deviation_threshold", 0.002))
        self.strategy_name = "grid"
        self.min_contract_size = config.get("min_contract_size", 1)
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
                
            # Get OHLCV and ticker in parallel
            ohlcv_future = self.api.fetch_ohlcv_with_retry(
                symbol, 
                self.timeframe, 
                limit=self.lookback + 1,
                max_retries=3
            )
            ticker_future = self.api.fetch_ticker_with_retry(symbol, max_retries=3)
            
            ohlcv, ticker = await asyncio.gather(ohlcv_future, ticker_future)
            
            if not ohlcv or not ticker:
                return None

            # Calculate average price
            closes = np.array([c[4] for c in ohlcv[-self.lookback:]], dtype=float)
            avg_price = np.mean(closes)
            
            # Get current prices
            bid = float(ticker.get("bid", 0))
            ask = float(ticker.get("ask", 0))
            if bid <= 0 or ask <= 0:
                return None
                
            mid_price = (bid + ask) / 2
            price_scale = await self._get_price_scale(symbol)
            size = await self.executor.calculate_risk_adjusted_size(
                symbol, 
                mid_price,
                price_scale
            )
            
            if size < self.min_contract_size:
                return None

            # Grid signals
            if mid_price < avg_price * (1 - self.threshold):
                log.info("[%s] BUY %s @ %.4f (Below avg: %.4f)", 
                         self.strategy_name.upper(), symbol, mid_price, avg_price)
                return ("buy", size)
            elif mid_price > avg_price * (1 + self.threshold):
                log.info("[%s] SELL %s @ %.4f (Above avg: %.4f)", 
                         self.strategy_name.upper(), symbol, mid_price, avg_price)
                return ("sell", size)
                
        except self.api.RateLimitExceeded:
            log.warning("[%s] Rate limit exceeded for %s", self.strategy_name.upper(), symbol)
            await asyncio.sleep(10)
        except Exception as e:
            log.exception("[%s] Error for %s: %s", self.strategy_name.upper(), symbol, e)
        return None
