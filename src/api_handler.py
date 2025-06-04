import asyncio
import logging
import time  # Imported the time module
from typing import List, Optional
import ccxt.async_support as ccxt
from ccxt import NetworkError, ExchangeError, RequestTimeout

logger = logging.getLogger("ApiHandler")

class ApiHandler:
    def __init__(self, api_key, api_secret, config=None):
        self.config = config or {}
        self.testnet = self.config.get("testnet", False)
        self.exchange = self._init_exchange(api_key, api_secret)
        self.market_map = {}
        self.price_scales = {}
        self.last_market_load = 0
        self.semaphore = asyncio.Semaphore(5)  # Rate limiting
        
    def _init_exchange(self, api_key, api_secret):
        params = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": self.config.get("default_type", "swap"),
                "adjustForTimeDifference": True,
            }
        }
        if self.testnet:
            params["urls"] = {"api": "https://testnet-api.phemex.com"}
        return ccxt.phemex(params)
        
    async def load_markets(self, reload=False):
        """Load markets with caching and automatic refresh"""
        current_time = time.time()
        if reload or not self.market_map or (current_time - self.last_market_load) > 3600:
            try:
                markets = await self.exchange.load_markets()
                self.market_map = {symbol: market["id"] for symbol, market in markets.items()}
                self.price_scales = {
                    symbol: 10 ** market["precision"]["price"]
                    for symbol, market in markets.items()
                }
                self.last_market_load = current_time
                logger.info("Loaded %d markets", len(markets))
            except Exception as e:
                logger.error("Market load failed: %s", e)
                
    async def get_price_scale(self, symbol):
        """Get price scale factor for Phemex (10^precision)"""
        if symbol not in self.price_scales:
            await self.load_markets(reload=True)
        return self.price_scales.get(symbol, 100)  # Default to 2 decimals
        
    async def get_contract_size(self, symbol):
        """Get contract size for position sizing"""
        await self.load_markets()
        market = self.exchange.markets.get(symbol)
        return market.get("contractSize", 1) if market else 1
        
    async def fetch_ohlcv_robust(self, symbol, timeframe="1m", limit=100, retries=3):
        """Fetch OHLCV with retry logic"""
        for attempt in range(retries):
            try:
                async with self.semaphore:
                    return await self.exchange.fetch_ohlcv(
                        symbol, timeframe, limit=limit
                    )
            except (NetworkError, ExchangeError, RequestTimeout) as e:
                if attempt == retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning("OHLCV attempt %d failed: %s - retrying in %ds", 
                              attempt+1, e, wait)
                await asyncio.sleep(wait)
        return []
        
    async def get_ohlcv(self, symbol: str, timeframe: str = '1m', limit: int = 20) -> List[List[float]]:
        """Alias for fetch_ohlcv_robust with default parameters"""
        return await self.fetch_ohlcv_robust(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit
        )
        
    async def place_order(self, symbol, side, order_type, quantity, price_ep=None, ioc_timeout=1000):
        """Place order with proper Phemex parameters"""
        try:
            params = {}
            if price_ep:
                params["priceEp"] = price_ep
            if ioc_timeout and order_type == "limit":
                params["timeInForce"] = "IOC"
                
            async with self.semaphore:
                return await self.exchange.create_order(
                    symbol,
                    order_type,
                    side,
                    quantity,
                    None,  # price should be in priceEp
                    params
                )
        except Exception as e:
            logger.error("Order failed: %s %s %s @ %s: %s", 
                         symbol, order_type, side, price_ep, e)
            return {"status": "error", "error": str(e)}
            
    async def cancel_order(self, symbol, order_id):
        """Cancel order on exchange"""
        try:
            async with self.semaphore:
                return await self.exchange.cancel_order(order_id, symbol)
        except Exception as e:
            logger.error("Cancel failed for %s: %s", order_id, e)
            
    async def fetch_positions(self):
        """Fetch open positions from exchange"""
        try:
            async with self.semaphore:
                return await self.exchange.fetch_positions()
        except Exception as e:
            logger.error("Position fetch failed: %s", e)
            return {}
            
    async def fetch_ticker(self, symbol):
        """Fetch ticker data"""
        try:
            async with self.semaphore:
                return await self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error("Ticker fetch failed for %s: %s", symbol, e)
            return None
            
    async def fetch_balance(self):
        """Fetch account balance"""
        try:
            async with self.semaphore:
                return await self.exchange.fetch_balance()
        except Exception as e:
            logger.error("Balance fetch failed: %s", e)
            return {}
