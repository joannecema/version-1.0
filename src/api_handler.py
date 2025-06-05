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
        self.semaphore = asyncio.Semaphore(5)  # Limit concurrent calls
       
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
                # Build symbol→market ID map (e.g. "BTC/USDT" → "BTCUSDT")
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
        """Convert timeframe string to seconds (e.g. "1m" → 60)"""
        units = {
            's': 1,
            'm': 60,
            'h': 3600,
            'd': 86400,
            'w': 604800,
        }
        unit = timeframe[-1]
        value = int(timeframe[:-1])
        return value * units.get(unit, 1)
        
    async def fetch_ohlcv_robust(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: Optional[int] = None,
        limit: Optional[int] = None,
        params: Optional[Dict[str, Any]] = None,
        retries: int = 3
    ) -> List[List[float]]:
        """Fetch OHLCV bars with retry logic and correct Phemex parameter handling."""
        # Ensure markets are loaded so market_map is populated
        if not self.market_map:
            await self.load_markets()

        # Translate symbol to Phemex market ID if available
        market_id = self.market_map.get(symbol, symbol)
        params = params.copy() if params else {}

        # Handle Phemex-specific 'to' parameter if since is provided
        if self.exchange.id == 'phemex' and since is not None:
            to_timestamp = int(time.time() * 1000)  # use ms
            if limit is not None:
                # Calculate end time based on timeframe and limit
                timeframe_sec = self._timeframe_to_seconds(timeframe)
                # since is in ms, convert to seconds, then back to ms after adding
                to_timestamp = since + (limit * timeframe_sec * 1000)
            params['to'] = to_timestamp

        # Always pass 'limit' inside params instead of as positional
        if limit is not None:
            params['limit'] = limit

        for attempt in range(retries):
            try:
                async with self.semaphore:
                    # Note: we pass since=None and limit=None positionally,
                    # and put both into params to avoid Phemex ccxt bug
                    return await self.exchange.fetch_ohlcv(
                        market_id,
                        timeframe,
                        since=None,
                        limit=None,
                        params=params
                    )
            except (NetworkError, ExchangeError, RequestTimeout) as e:
                if attempt == retries - 1:
                    logger.error("OHLCV ultimately failed for %s: %s", symbol, e)
                    raise
                wait = 2 ** attempt
                logger.warning(
                    "OHLCV attempt %d for %s failed: %s — retrying in %ds",
                    attempt + 1, symbol, e, wait
                )
                await asyncio.sleep(wait)
        return []
        
    async def get_ohlcv(
        self, 
        symbol: str, 
        timeframe: str = '1m', 
        limit: int = 20,
        since: Optional[int] = None
    ) -> List[List[float]]:
        """Get OHLCV data using the robust fetch method."""
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
        """Place an order on Phemex with the correct param usage."""
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
                    None,  # Price is encoded via priceEp in params
                    params
                )
        except Exception as e:
            logger.error(
                "Order failed: symbol=%s, type=%s, side=%s, priceEp=%s, error=%s", 
                symbol, order_type, side, price_ep, e
            )
            return {"status": "error", "error": str(e)}
            
    async def cancel_order(self, symbol: str, order_id: str) -> Optional[Dict[str, Any]]:
        """Cancel an existing order on Phemex."""
        try:
            async with self.semaphore:
                return await self.exchange.cancel_order(order_id, symbol)
        except Exception as e:
            logger.error("Cancel failed for %s: %s", order_id, e)
            return None
            
    async def fetch_positions(self) -> Dict[str, Any]:
        """Fetch all open positions."""
        try:
            async with self.semaphore:
                return await self.exchange.fetch_positions()
        except Exception as e:
            logger.error("Position fetch failed: %s", e)
            return {}
            
    async def fetch_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch the latest ticker for a symbol."""
        try:
            async with self.semaphore:
                return await self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error("Ticker fetch failed for %s: %s", symbol, e)
            return None
            
    async def fetch_balance(self) -> Dict[str, Any]:
        """Fetch account balances."""
        try:
            async with self.semaphore:
                return await self.exchange.fetch_balance()
        except Exception as e:
            logger.error("Balance fetch failed: %s", e)
            return {}
