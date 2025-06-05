import asyncio
import logging
import time
from typing import List, Optional, Dict, Any
import ccxt.async_support as ccxt
from ccxt import NetworkError, ExchangeError, RequestTimeout

logger = logging.getLogger("ApiHandler")

class ApiHandler:
    def __init__(self, api_key, api_secret, config=None):
        self.config = config or {}
        self.testnet = self.config.get("testnet", False)
        self.exchange = self._init_exchange(api_key, api_secret)
        self.market_map: Dict[str, str] = {}
        self.price_scales: Dict[str, int] = {}
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
                
    async def get_price_scale(self, symbol: str) -> int:
        """Get price scale factor for Phemex (10^precision)"""
        if symbol not in self.price_scales:
            await self.load_markets(reload=True)
        return self.price_scales.get(symbol, 100)  # Default to 2 decimals
        
    async def get_contract_size(self, symbol: str) -> float:
        """Get contract size for position sizing"""
        await self.load_markets()
        market = self.exchange.markets.get(symbol)
        return market.get("contractSize", 1.0) if market else 1.0
        
    def _timeframe_to_seconds(self, timeframe: str) -> int:
        """Convert timeframe string to seconds"""
        units = {
            's': 1,
            'm': 60,
            'h': 3600,
            'd': 86400,
            'w': 604800,
        }
        unit = timeframe[-1]
        value = int(timeframe[:-1])
        return value * units[unit]
        
    async def fetch_ohlcv_robust(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: Optional[int] = None,
        limit: Optional[int] = None,
        params: Optional[Dict[str, Any]] = None,
        retries: int = 3
    ) -> List[List[float]]:
        """Fetch OHLCV with retry logic and Phemex parameter handling"""
        params = params or {}
        exchange_id = self.exchange.id
        
        # Handle Phemex-specific parameters
        if exchange_id == 'phemex' and since is not None:
            # Calculate 'to' timestamp (current time if limit not provided)
            to_timestamp = int(time.time())
            
            if limit is not None:
                # Calculate end time based on timeframe and limit
                timeframe_sec = self._timeframe_to_seconds(timeframe)
                to_timestamp = int(since / 1000) + (limit * timeframe_sec)
            
            params['to'] = to_timestamp
        
        for attempt in range(retries):
            try:
                async with self.semaphore:
                    return await self.exchange.fetch_ohlcv(
                        symbol, 
                        timeframe, 
                        since=since, 
                        limit=limit, 
                        params=params
                    )
            except (NetworkError, ExchangeError, RequestTimeout) as e:
                if attempt == retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning("OHLCV attempt %d failed: %s - retrying in %ds", 
                              attempt+1, e, wait)
                await asyncio.sleep(wait)
        return []
        
    async def get_ohlcv(
        self, 
        symbol: str, 
        timeframe: str = '1m', 
        limit: int = 20,
        since: Optional[int] = None
    ) -> List[List[float]]:
        """Get OHLCV data with robust fetching"""
        return await self.fetch_ohlcv_robust(
            symbol=symbol,
            timeframe=timeframe,
            since=since,
            limit=limit
        )
        
    async def place_order(
        self, 
        symbol: str, 
        side: str, 
        order_type: str, 
        quantity: float, 
        price_ep: Optional[int] = None, 
        ioc_timeout: int = 1000
    ) -> Dict[str, Any]:
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
            
    async def cancel_order(self, symbol: str, order_id: str) -> Optional[Dict[str, Any]]:
        """Cancel order on exchange"""
        try:
            async with self.semaphore:
                return await self.exchange.cancel_order(order_id, symbol)
        except Exception as e:
            logger.error("Cancel failed for %s: %s", order_id, e)
            return None
            
    async def fetch_positions(self) -> Dict[str, Any]:
        """Fetch open positions from exchange"""
        try:
            async with self.semaphore:
                return await self.exchange.fetch_positions()
        except Exception as e:
            logger.error("Position fetch failed: %s", e)
            return {}
            
    async def fetch_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch ticker data"""
        try:
            async with self.semaphore:
                return await self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error("Ticker fetch failed for %s: %s", symbol, e)
            return None
            
    async def fetch_balance(self) -> Dict[str, Any]:
        """Fetch account balance"""
        try:
            async with self.semaphore:
                return await self.exchange.fetch_balance()
        except Exception as e:
            logger.error("Balance fetch failed: %s", e)
            return {}
