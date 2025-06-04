import asyncio
import logging
import time
import ccxt.async_support as ccxt_async  # Corrected import
from ccxt import NetworkError, ExchangeError, RequestTimeout

logger = logging.getLogger("ApiHandler")

class ApiHandler:
    def __init__(self, api_key, api_secret, config=None):
        self.config = config or {}
        self.exchange_name = self.config.get("exchange", {}).get("name", "phemex")
        self.testnet = self.config.get("exchange", {}).get("testnet", False)
        self.exchange = self._init_exchange(api_key, api_secret)
        self.market_map = {}
        self.price_scales = {}
        self.last_market_load = 0
        self.semaphore = asyncio.Semaphore(self.config.get("max_concurrent_requests", 5))
        
    def _init_exchange(self, api_key, api_secret):
        """Dynamically initialize exchange based on config"""
        exchange_class = getattr(ccxt_async, self.exchange_name, None)
        if not exchange_class:
            raise ValueError(f"Unsupported exchange: {self.exchange_name}")
        
        params = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": self.config.get("default_type", "swap"),
                "adjustForTimeDifference": True,
                "test": self.testnet,  # Unified testnet handling
            }
        }
        
        # Phemex-specific configuration
        if self.exchange_name == "phemex" and self.testnet:
            params["urls"] = {"api": "https://testnet-api.phemex.com"}
        
        # WebSocket configuration
        if self.config.get("disable_ws", False):
            params["options"]["ws"] = False
            
        return exchange_class(params)
        
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
                # Fallback to default symbols if available
                if self.config.get("symbols"):
                    self.market_map = {s: s for s in self.config["symbols"]}
                
    async def get_price_scale(self, symbol):
        """Get price scale factor (10^precision)"""
        if symbol not in self.price_scales:
            await self.load_markets(reload=True)
        return self.price_scales.get(symbol, 100)  # Default to 2 decimals
        
    async def get_contract_size(self, symbol):
        """Get contract size for position sizing"""
        await self.load_markets()
        market = self.exchange.markets.get(symbol)
        return market.get("contractSize", 1) if market else 1
        
    async def fetch_ohlcv_robust(self, symbol, timeframe="1m", limit=100, retries=3):
        """Fetch OHLCV with exponential backoff"""
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
        
    async def place_order(self, symbol, side, order_type, quantity, price=None, price_ep=None, ioc_timeout=1000):
        """Universal order placement with exchange-agnostic parameters"""
        try:
            params = {}
            
            # Handle Phemex-specific price encoding
            if self.exchange_name == "phemex" and price_ep:
                params["priceEp"] = price_ep
            elif price:
                # For other exchanges
                params["price"] = price
            
            # Handle IOC orders
            if ioc_timeout and order_type == "limit":
                params["timeInForce"] = "IOC"
                
            async with self.semaphore:
                return await self.exchange.create_order(
                    symbol,
                    order_type,
                    side,
                    quantity,
                    price,  # Use normal price for non-Phemex
                    params
                )
        except Exception as e:
            logger.error("Order failed: %s %s %s @ %s: %s", 
                         symbol, order_type, side, price_ep or price, e)
            return {"status": "error", "error": str(e)}
            
    async def cancel_order(self, symbol, order_id):
        """Cancel order with error handling"""
        try:
            async with self.semaphore:
                return await self.exchange.cancel_order(order_id, symbol)
        except Exception as e:
            logger.error("Cancel failed for %s: %s", order_id, e)
            return False
            
    async def fetch_positions(self):
        """Fetch positions with rate limiting"""
        try:
            async with self.semaphore:
                return await self.exchange.fetch_positions()
        except Exception as e:
            logger.error("Position fetch failed: %s", e)
            return {}
    
    async def close(self):
        """Properly close exchange connection"""
        await self.exchange.close()
        logger.info("Exchange connection closed")
