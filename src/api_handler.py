import asyncio
import logging
import time
from typing import List, Optional, Dict, Any
import ccxt.async_support as ccxt
from ccxt import NetworkError, ExchangeError, RequestTimeout, BadRequest

logger = logging.getLogger("ApiHandler")

class ApiHandler:
    # ... (rest of __init__ remains unchanged)
    
    async def fetch_ohlcv_robust(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: Optional[int] = None,
        limit: Optional[int] = None,
        params: Optional[Dict[str, Any]] = None,
        retries: int = 3
    ) -> List[List[float]]:
        """Fetch OHLCV bars with retry logic and robust limit handling."""
        # Ensure markets are loaded
        if not self.market_map:
            await self.load_markets()
        
        # Validate symbol exists
        if symbol not in self.market_map:
            await self.load_markets(reload=True)
            if symbol not in self.market_map:
                logger.error("Symbol %s not found in market map", symbol)
                return []
        
        params = params.copy() if params else {}

        # Handle Phemex-specific parameters
        if self.exchange.id == 'phemex':
            now_ms = int(time.time() * 1000)
            # Calculate 'to' parameter if needed
            if since is not None and limit is not None:
                timeframe_sec = self._timeframe_to_seconds(timeframe)
                to_time = since + (limit * timeframe_sec * 1000)
                params['to'] = min(to_time, now_ms)
            else:
                params['to'] = now_ms

            # SAFETY: Validate and adjust limit for Phemex
            if limit is not None:
                market = self.exchange.markets.get(symbol)
                max_limit = self._get_safe_max_limit(market)
                limit = min(limit, max_limit)

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
            except BadRequest as e:
                logger.error("BadRequest for %s: %s", symbol, e)
                raise
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
            except TypeError as e:
                if "'<' not supported" in str(e) and "int' and 'dict'" in str(e):
                    logger.warning("TypeError detected for %s: %s", symbol, e)
                    # Apply safe limit and retry immediately
                    if self.exchange.id == 'phemex' and limit is not None:
                        market = self.exchange.markets.get(symbol)
                        max_limit = self._get_safe_max_limit(market)
                        limit = min(limit, max_limit)
                        logger.info("Adjusted limit for %s to %d", symbol, limit)
                        continue  # Retry immediately with fixed limit
                raise  # Re-raise if not our specific error
        return []

    def _get_safe_max_limit(self, market: Dict[str, Any]) -> int:
        """Safely extract max limit from market data with fallbacks"""
        try:
            # Handle different market data structures
            if 'limits' in market and 'amount' in market['limits']:
                max_limit = market['limits']['amount']['max']
                
                # Handle unexpected data types
                if isinstance(max_limit, dict):
                    return 500  # Default safe value
                if isinstance(max_limit, (int, float)):
                    return int(max_limit)
        except (KeyError, TypeError):
            pass
        return 500  # Fallback to conservative default
    
    # ... (rest of the class remains unchanged)
