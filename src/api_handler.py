import asyncio
import logging
import time
from typing import List, Optional, Dict, Any
import ccxt.async_support as ccxt
from ccxt import NetworkError, ExchangeError, RequestTimeout, BadRequest

logger = logging.getLogger("ApiHandler")

class ApiHandler:
    def __init__(self, api_key, api_secret, config=None):
        self.config = config or {}
        self.testnet = self.config.get("testnet", False)
        self.exchange = self._init_exchange(api_key, api_secret)
        self.market_map: Dict[str, str] = {}
        self.price_scales: Dict[str, int] = {}
        self.last_market_load = 0
        self.semaphore = asyncio.Semaphore(5)
        self.market_load_lock = asyncio.Lock()
       
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
        """Load markets with robust error handling for precision data"""
        current_time = time.time()
        if reload or not self.market_map or (current_time - self.last_market_load) > 3600:
            async with self.market_load_lock:
                if reload or not self.market_map or (current_time - self.last_market_load) > 3600:
                    try:
                        markets = await self.exchange.load_markets()
                        self.market_map = {}
                        self.price_scales = {}
                        
                        for symbol, market in markets.items():
                            try:
                                # Handle market ID mapping
                                self.market_map[symbol] = market["id"]
                                
                                # Robust precision handling
                                precision = market.get("precision", {})
                                price_precision = precision.get("price")
                                
                                if price_precision is None:
                                    logger.warning(f"Price precision is None for {symbol}, using default")
                                    self.price_scales[symbol] = 100  # Default to 2 decimals
                                elif isinstance(price_precision, (int, float)):
                                    self.price_scales[symbol] = 10 ** int(price_precision)
                                else:
                                    logger.warning(f"Unexpected price precision type for {symbol}: {type(price_precision)}")
                                    self.price_scales[symbol] = 100
                            except Exception as e:
                                logger.error(f"Error processing market {symbol}: {str(e)}")
                                self.price_scales[symbol] = 100
                        
                        self.last_market_load = current_time
                        logger.info(f"Loaded {len(markets)} markets")
                    except Exception as e:
                        logger.error(f"Market load failed: {str(e)}")
                        # Preserve existing markets if available
                        if not self.market_map:
                            self.market_map = {}
                            self.price_scales = {}
                
    async def get_price_scale(self, symbol: str) -> int:
        """Get price scale with automatic reload on failure"""
        if symbol not in self.price_scales:
            await self.load_markets(reload=True)
        return self.price_scales.get(symbol, 100)
        
    async def get_contract_size(self, symbol: str) -> float:
        """Get contract size with fallback"""
        await self.load_markets()
        market = self.exchange.markets.get(symbol)
        return market.get("contractSize", 1.0) if market else 1.0
        
    def _timeframe_to_seconds(self, timeframe: str) -> int:
        units = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}
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
        """Fetch OHLCV with comprehensive error handling"""
        # Ensure markets are loaded
        if not self.market_map:
            await self.load_markets()
        
        # Validate symbol exists
        if symbol not in self.market_map:
            await self.load_markets(reload=True)
            if symbol not in self.market_map:
                logger.error(f"Symbol {symbol} not found in market map")
                return []
        
        params = params.copy() if params else {}
        original_limit = limit  # Store for error handling

        # Phemex-specific parameters
        if self.exchange.id == 'phemex':
            now_ms = int(time.time() * 1000)
            if since is not None and limit is not None:
                timeframe_sec = self._timeframe_to_seconds(timeframe)
                to_time = since + (limit * timeframe_sec * 1000)
                params['to'] = min(to_time, now_ms)
            else:
                params['to'] = now_ms

            # Handle limit adjustment safely
            if limit is not None:
                try:
                    market = self.exchange.markets.get(symbol)
                    if market:
                        max_limit = self._get_safe_max_limit(market)
                        # Final type check before min operation
                        if not isinstance(max_limit, (int, float)):
                            logger.warning(f"Unexpected max_limit type for {symbol}: {type(max_limit)}")
                            max_limit = 500
                        limit = min(limit, int(max_limit))
                except Exception as e:
                    logger.error(f"Limit adjustment failed for {symbol}: {str(e)}")
                    limit = min(limit, 500) if limit else None

        for attempt in range(retries):
            try:
                async with self.semaphore:
                    return await self.exchange.fetch_ohlcv(
                        symbol, timeframe, since, limit, params
                    )
            except BadRequest as e:
                logger.error(f"BadRequest for {symbol}: {str(e)}")
                raise
            except (NetworkError, ExchangeError, RequestTimeout) as e:
                if attempt == retries - 1:
                    logger.error(f"OHLCV failed for {symbol}: {str(e)}")
                    raise
                wait = 2 ** attempt
                logger.warning(f"OHLCV attempt {attempt+1} for {symbol} failed: {str(e)} - retrying in {wait}s")
                await asyncio.sleep(wait)
            except TypeError as e:
                error_str = str(e)
                if "'<' not supported" in error_str and "int' and 'dict'" in error_str:
                    logger.warning(f"TypeError detected for {symbol}: {error_str}")
                    if self.exchange.id == 'phemex' and original_limit is not None:
                        logger.info(f"Applying emergency limit fix for {symbol}")
                        limit = min(original_limit, 500)
                        continue
                logger.error(f"Unhandled TypeError for {symbol}: {error_str}")
                raise
        return []
        
    def _get_safe_max_limit(self, market: Dict[str, Any]) -> int:
        """Robust limit extraction with multiple fallbacks"""
        try:
            # Check for nested limit structure
            limits = market.get('limits', {})
            amount = limits.get('amount', {})
            max_limit = amount.get('max')
            
            # Handle different data types
            if isinstance(max_limit, dict):
                logger.debug(f"Found dict max_limit for {market['symbol']}")
                return 500
            elif isinstance(max_limit, (int, float)):
                return int(max_limit)
            elif isinstance(max_limit, str) and max_limit.isdigit():
                return int(max_limit)
                
            # Check alternative locations
            if 'info' in market:
                info = market['info']
                if 'maxOrderQty' in info:
                    return int(info['maxOrderQty'])
        except Exception as e:
            logger.debug(f"Limit extraction error: {str(e)}")
            
        return 500  # Ultimate fallback
        
    async def get_ohlcv(
        self, 
        symbol: str, 
        timeframe: str = '1m', 
        limit: int = 20,
        since: Optional[int] = None
    ) -> List[List[float]]:
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
        try:
            params = {}
            if price_ep:
                params["priceEp"] = price_ep
            if ioc_timeout and order_type == "limit":
                params["timeInForce"] = "IOC"
            async with self.semaphore:
                return await self.exchange.create_order(
                    symbol, order_type, side, quantity, None, params
                )
        except Exception as e:
            logger.error(f"Order failed: {symbol}, {order_type}, {side}, {price_ep} - {str(e)}")
            return {"status": "error", "error": str(e)}
            
    async def cancel_order(self, symbol: str, order_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with self.semaphore:
                return await self.exchange.cancel_order(order_id, symbol)
        except Exception as e:
            logger.error(f"Cancel failed for {order_id}: {str(e)}")
            return None
            
    async def fetch_positions(self) -> Dict[str, Any]:
        try:
            async with self.semaphore:
                return await self.exchange.fetch_positions()
        except Exception as e:
            logger.error(f"Position fetch failed: {str(e)}")
            return {}
            
    async def fetch_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            async with self.semaphore:
                return await self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"Ticker fetch failed for {symbol}: {str(e)}")
            return None
            
    async def fetch_balance(self) -> Dict[str, Any]:
        try:
            async with self.semaphore:
                # Phemex requires additional parameters
                params = {}
                if self.exchange.id == 'phemex':
                    params['code'] = 'USD'  # Or appropriate currency
                return await self.exchange.fetch_balance(params)
        except Exception as e:
            logger.error(f"Balance fetch failed: {str(e)}")
            return {}
