# src/api_handler.py

import asyncio
import logging
import random
import time
import hashlib
import ccxt.async_support as ccxt_async
import ccxt.pro as ccxtpro
from functools import wraps
from typing import List, Dict, Optional

logger = logging.getLogger("ApiHandler")


def retry(fn):
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        last_exc = None
        for i in range(5):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                last_exc = e
                wait = (2 ** i) + random.uniform(0, 1)
                logger.warning(
                    f"[RETRY] {fn.__name__} failed (attempt {i + 1}): {e} — retrying in {wait:.2f}s"
                )
                await asyncio.sleep(wait)
        logger.error(f"[RETRY] {fn.__name__} ultimately failed: {last_exc}")
        raise last_exc

    return wrapped


class ApiHandler:
    def __init__(self, api_key: str, api_secret: str, cfg: dict):
        """
        Merged ApiHandler combining enhanced REST methods (ccxt.async_support) with
        ccxt.pro websocket capabilities. Handles:
          • throttling
          • cached market IDs
          • price precision scaling
          • robust OHLCV fetching
          • synchronous wrappers for trade_executor
        """
        self.cfg = cfg
        self.disable_ws = cfg.get("disable_ws", False)
        self.testnet = cfg.get("testnet", False)
        self.throttle = asyncio.Semaphore(cfg.get("max_concurrent_requests", 3))

        # REST client for robust order creation and OHLCV
        self.rest_exchange = self._init_rest_exchange(api_key, api_secret)

        # Websocket-enabled client for live data (tickers, OHLCV, order books, orders)
        self.ws_exchange = ccxtpro.phemex(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
                **({"urls": {"api": "https://testnet-api.phemex.com"}} if self.testnet else {}),
            }
        )

        self.market_map: Dict[str, str] = {}      # symbol → market_id
        self.price_scales: Dict[str, int] = {}    # symbol → 10**precision.price
        self.last_market_load = 0

    def _init_rest_exchange(self, api_key: str, api_secret: str):
        params = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": self.cfg.get("default_type", "spot"),
                "adjustForTimeDifference": True,
            },
        }
        if self.testnet:
            params["urls"] = {"api": "https://testnet-api.phemex.com"}
        return ccxt_async.phemex(params)

    async def load_markets(self, reload: bool = False):
        """
        Load or reload markets into both REST and WS clients, caching
        symbol→market_id and price precision scales.
        """
        now = time.time()
        if reload or not self.market_map or (now - self.last_market_load) > 3600:
            try:
                await self.ws_exchange.load_markets()
                self.market_map = {
                    symbol: market["id"]
                    for symbol, market in self.ws_exchange.markets.items()
                    if market.get("spot", True)
                }
                for symbol, market in self.ws_exchange.markets.items():
                    prec = market.get("precision", {}).get("price", 2)
                    self.price_scales[symbol] = 10 ** prec

                self.last_market_load = now
                logger.info(f"[API] ✅ Loaded {len(self.market_map)} spot symbols from Phemex")
            except Exception as e:
                logger.error(f"[API] ❌ Failed to load markets: {e}")
                raise

    def get_market_id(self, symbol: str) -> str:
        """
        Returns the exchange-specific market ID for a given symbol,
        falling back to removing '/' if not found.
        """
        if symbol in self.market_map:
            return self.market_map[symbol]
        try:
            market = self.ws_exchange.market(symbol)
            return market["id"]
        except Exception as e:
            logger.warning(f"[API] ⚠️ Symbol resolution fallback for {symbol}: {e}")
            return symbol.replace("/", "")

    async def get_price_scale(self, symbol: str) -> int:
        """
        Return 10**precision.price for the given symbol. If missing, reload markets.
        """
        if symbol not in self.price_scales:
            await self.load_markets(reload=True)
        return self.price_scales.get(symbol, 100)

    @retry
    async def fetch_ohlcv(self, symbol: str, timeframe: str, since=None, limit=None, params=None):
        """
        Fetch OHLCV via REST with retry and throttling.
        """
        async with self.throttle:
            try:
                market_id = self.get_market_id(symbol)
                query = params.copy() if params else {}
                query["to"] = int(time.time() * 1000)
                return await self.rest_exchange.fetch_ohlcv(market_id, timeframe, since, limit, query)
            except Exception as e:
                logger.error(f"[API] fetch_ohlcv failed for {symbol}: {e}")
                return []

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int):
        """
        Attempt websocket subscription first (if enabled); fallback to REST.
        """
        try:
            if not self.disable_ws:
                return await self.watch_ohlcv(symbol, timeframe, limit)
        except Exception as e:
            logger.warning(f"[API] watch_ohlcv failed for {symbol}, falling back to REST: {e}")
        await asyncio.sleep(0.25)
        to_ts = int(time.time() * 1000)
        seconds = self.ws_exchange.parse_timeframe(timeframe)
        from_ts = to_ts - limit * seconds * 1000
        return await self.fetch_ohlcv(symbol, timeframe, since=from_ts, limit=limit)

    @retry
    async def watch_ohlcv(self, symbol: str, timeframe: str, limit: int):
        """
        Subscribe to OHLCV via WebSocket.
        """
        if self.disable_ws:
            raise RuntimeError("WebSocket is disabled by config.")
        market_id = self.get_market_id(symbol)
        return await self.ws_exchange.watch_ohlcv(market_id, timeframe, limit)

    @retry
    async def fetch_ticker(self, symbol: str):
        """
        Fetch ticker via REST with throttling.
        """
        async with self.throttle:
            market_id = self.get_market_id(symbol)
            return await self.ws_exchange.fetch_ticker(market_id)

    async def get_ticker(self, symbol: str):
        """
        Attempt websocket subscription first (if enabled); fallback to REST.
        """
        try:
            if not self.disable_ws:
                return await self.watch_ticker(symbol)
        except Exception as e:
            logger.warning(f"[API] WebSocket ticker failed for {symbol}, fallback to REST: {e}")
        try:
            return await self.fetch_ticker(symbol)
        except Exception as e2:
            logger.error(f"[API] fetch_ticker also failed for {symbol}: {e2}")
            return None

    @retry
    async def watch_ticker(self, symbol: str):
        """
        Subscribe to ticker via WebSocket.
        """
        if self.disable_ws:
            raise RuntimeError("WebSocket is disabled by config.")
        market_id = self.get_market_id(symbol)
        return await self.ws_exchange.watch_ticker(market_id)

    @retry
    async def fetch_order_book(self, symbol: str, limit: int = 5):
        """
        Fetch order book, preferring WebSocket. Fallback to REST if needed.
        """
        market_id = self.get_market_id(symbol)
        if self.disable_ws:
            logger.warning(f"[API] WebSocket is disabled — using REST for {symbol}")
            try:
                async with self.throttle:
                    return await self.ws_exchange.fetch_order_book(market_id, limit=limit)
            except Exception as e:
                logger.error(f"[API] REST fetch_order_book failed for {symbol}: {e}")
                return None
        else:
            try:
                return await self.ws_exchange.watch_order_book(market_id, limit)
            except Exception as e:
                logger.warning(f"[API] WebSocket order book failed for {symbol}, fallback to REST: {e}")
                try:
                    async with self.throttle:
                        return await self.ws_exchange.fetch_order_book(market_id, limit=limit)
                except Exception as e2:
                    logger.error(f"[API] REST fallback also failed for {symbol}: {e2}")
                    return None

    async def create_market_order(self, symbol: str, side: str, amount: float):
        """
        Place a market order (via WS). Rounds amount to the correct precision.
        """
        try:
            async with self.throttle:
                market_id = self.get_market_id(symbol)
                market = self.ws_exchange.market(symbol)
                prec_amt = market["precision"].get("amount", 8)
                amt = round(float(amount), prec_amt)
                logger.debug(f"[API] Market order → {side.upper()} {symbol} qty={amt}")
                return await self.ws_exchange.create_order(market_id, "market", side, amt)
        except Exception as e:
            logger.error(f"[API] Failed to place market order for {symbol}: {e}")
            return None

    async def create_limit_order(self, symbol: str, side: str, amount: float, price: float, params: dict):
        """
        Place a limit order (via WS), rounding both amount and price to correct precisions.
        """
        try:
            async with self.throttle:
                market_id = self.get_market_id(symbol)
                market = self.ws_exchange.market(symbol)
                prec_amt = market["precision"].get("amount", 8)
                prec_prc = market["precision"].get("price", 8)
                amt = round(float(amount), prec_amt)
                prc = round(float(price), prec_prc)

                clean_params = {}
                for k, v in (params or {}).items():
                    if isinstance(v, float):
                        try:
                            clean_params[k] = int(float(v))
                        except Exception:
                            clean_params[k] = str(int(round(v)))
                    else:
                        clean_params[k] = v

                logger.debug(
                    f"[API] Payload → {side.upper()} {symbol} qty={amt} price={prc} PARAMS={clean_params}"
                )
                order = await self.ws_exchange.create_order(
                    market_id, "limit", side, amt, prc, clean_params
                )
                logger.info(f"[API] Order placed → ID={order.get('id')} STATUS={order.get('status')}")
                logger.debug(f"[API] Raw response: {order}")
                return order
        except Exception as e:
            logger.error(f"[API] Failed to place limit order for {symbol}: {e}")
            return None

    @retry
    async def fetch_open_orders(self, symbol: Optional[str] = None):
        """
        Fetch open orders for a symbol or all symbols.
        """
        try:
            async with self.throttle:
                if symbol:
                    market_id = self.get_market_id(symbol)
                    return await self.ws_exchange.fetch_open_orders(market_id)
                else:
                    return await self.ws_exchange.fetch_open_orders()
        except Exception as e:
            logger.error(f"[API] Failed to fetch open orders for {symbol or 'ALL'}: {e}")
            return []

    async def log_open_orders(self, symbol: Optional[str] = None):
        """
        Logs all currently open orders for a symbol or for all symbols.
        """
        orders = await self.fetch_open_orders(symbol)
        if not orders:
            logger.info(f"[ORDERS] No open orders for {symbol or 'ALL'}")
        else:
            for order in orders:
                logger.info(
                    f"[ORDERS] Open → ID={order.get('id')} SYMBOL={order.get('symbol')} "
                    f"SIDE={order.get('side')} PRICE={order.get('price')} AMOUNT={order.get('amount')} "
                    f"STATUS={order.get('status')} CREATED={order.get('datetime')}"
                )

    async def fetch_ohlcv_robust(self, symbol: str, timeframe: str = "1m", limit: int = 100, retries: int = 3):
        """
        A fallback wrapper that attempts multiple REST fetch_ohlcv calls with exponential backoff delays.
        """
        for i in range(retries):
            try:
                return await self.rest_exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            except (ccxt_async.NetworkError, ccxt_async.ExchangeError) as e:
                if i == retries - 1:
                    raise
                await asyncio.sleep(2 ** i)

    def get_reference_price(self, symbol: str) -> float:
        """
        Synchronous wrapper to fetch last price (ticker).
        """
        loop = asyncio.get_event_loop()
        ticker = loop.run_until_complete(self.get_ticker(symbol))
        if not ticker:
            raise RuntimeError(f"Could not fetch reference price for {symbol}")
        return float(ticker.get("last", ticker.get("close", 0.0)))

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        ioc_timeout: int = 1000,
    ) -> Dict:
        """
        Synchronous wrapper to place limit or market order.
        """
        loop = asyncio.get_event_loop()
        if order_type == "market":
            return loop.run_until_complete(self.create_market_order(symbol, side, quantity))
        else:
            # Calculate scaled priceEp if needed
            priceEp = None
            if price is not None:
                scale = loop.run_until_complete(self.get_price_scale(symbol))
                priceEp = int(price * scale)
            params = {"priceEp": priceEp, "timeInForce": "IOC"} if ioc_timeout else {}
            return loop.run_until_complete(self.create_limit_order(symbol, side, quantity, price, params))

    def cancel_order(self, symbol: str, order_id: str) -> None:
        """
        Synchronous wrapper to cancel an open order by ID.
        """
        loop = asyncio.get_event_loop()
        market_id = self.get_market_id(symbol)
        loop.run_until_complete(self.ws_exchange.cancel_order(market_id, order_id))

    async def close(self):
        """
        Gracefully close both REST and WS clients to avoid unclosed session warnings.
        """
        try:
            await self.rest_exchange.close()
        except Exception as e:
            logger.warning(f"[API] Failed to close REST exchange cleanly: {e}")

        try:
            await self.ws_exchange.close()
        except Exception as e:
            logger.warning(f"[API] Failed to close WS exchange cleanly: {e}")


# Alias PhemexAPI so imports in other files still work
PhemexAPI = ApiHandler
