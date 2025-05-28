import asyncio
import random
import time
import logging
import ccxt.pro as ccxtpro
from functools import wraps
from typing import List, Dict

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
                logger.warning(f"[RETRY] {fn.__name__} failed (attempt {i + 1}): {e} — retrying in {wait:.2f}s")
                await asyncio.sleep(wait)
        logger.error(f"[RETRY] {fn.__name__} ultimately failed: {last_exc}")
        raise last_exc
    return wrapped


class ApiHandler:
    def __init__(self, api_key, api_secret, cfg):
        self.cfg = cfg
        self.disable_ws = cfg.get("disable_ws", False)
        self.binance_enabled = True
        self.throttle = asyncio.Semaphore(cfg.get("max_concurrent_requests", 3))  # Throttling limit

        self.exchange = ccxtpro.phemex({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        })

        if cfg.get("enable_binance"):
            try:
                temp_binance = ccxtpro.binance()
                asyncio.create_task(self._check_binance_block(temp_binance))
            except Exception as e:
                logger.warning(f"[BINANCE] Could not initialize test client: {e}")
                self.binance_enabled = False

    async def _check_binance_block(self, client):
        try:
            await client.load_markets()
        except Exception as e:
            if "restricted location" in str(e).lower() or "451" in str(e):
                logger.error(f"[BINANCE] Blocked in region: {e}")
                self.binance_enabled = False
        finally:
            await client.close()

    @retry
    async def watch_ohlcv(self, symbol, timeframe, limit):
        if self.disable_ws:
            raise RuntimeError("WebSocket is disabled by config.")
        return await self.exchange.watch_ohlcv(symbol, timeframe, limit)

    @retry
    async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, params=None):
        async with self.throttle:
            return await self.exchange.fetch_ohlcv(symbol, timeframe, since, limit, params or {})

    async def get_ohlcv(self, symbol, timeframe, limit):
        try:
            if not self.disable_ws:
                return await self.watch_ohlcv(symbol, timeframe, limit)
        except Exception as e:
            logger.warning(f"[API] watch_ohlcv failed for {symbol}, fallback to REST: {e}")
        await asyncio.sleep(0.25)
        to_ts = int(time.time() * 1000)
        seconds = self.exchange.parse_timeframe(timeframe)
        from_ts = to_ts - limit * seconds * 1000
        try:
            return await self.fetch_ohlcv(symbol, timeframe, since=from_ts, limit=limit,
                                          params={"start": from_ts, "end": to_ts})
        except Exception as e2:
            logger.error(f"[API] fetch_ohlcv failed for {symbol}: {e2}")
            return []

    @retry
    async def watch_ticker(self, symbol):
        if self.disable_ws:
            raise RuntimeError("WebSocket is disabled by config.")
        return await self.exchange.watch_ticker(symbol)

    @retry
    async def fetch_ticker(self, symbol: str):
        async with self.throttle:
            return await self.exchange.fetch_ticker(symbol)

    async def get_ticker(self, symbol: str):
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
    async def fetch_tickers(self, symbols: List[str]) -> Dict[str, dict]:
        results = {}
        for symbol in symbols:
            try:
                ticker = await self.get_ticker(symbol)
                if ticker:
                    results[symbol] = ticker
            except Exception as e:
                logger.error(f"[API] Failed to fetch ticker for {symbol}: {e}")
        return results

    @retry
    async def fetch_order_book(self, symbol, limit=5):
        if self.disable_ws:
            logger.warning(f"[API] WebSocket is disabled — falling back to REST for {symbol}")
            try:
                async with self.throttle:
                    return await self.exchange.fetch_order_book(symbol, limit=limit)
            except Exception as e:
                logger.error(f"[API] REST fetch_order_book failed for {symbol}: {e}")
                return None
        else:
            try:
                return await self.exchange.watch_order_book(symbol, limit)
            except Exception as e:
                logger.warning(f"[API] WebSocket order book failed for {symbol}, fallback to REST: {e}")
                try:
                    async with self.throttle:
                        return await self.exchange.fetch_order_book(symbol, limit=limit)
                except Exception as e2:
                    logger.error(f"[API] REST fallback also failed for {symbol}: {e2}")
                    return None

    async def create_limit_order(self, symbol, side, amount, price, params):
        try:
            async with self.throttle:
                market = self.exchange.market(symbol)
                precision_amount = market['precision']['amount']
                precision_price = market['precision']['price']
                formatted_amount = float(f"{amount:.{precision_amount}f}")
                formatted_price = float(f"{price:.{precision_price}f}")
                logger.debug(f"[API] Payload → {side.upper()} {symbol} qty={formatted_amount} price={formatted_price} TIF={params}")
                order = await self.exchange.create_order(symbol, "limit", side, formatted_amount, formatted_price, params)
                logger.info(f"[API] Order placed → ID={order.get('id')} STATUS={order.get('status')}")
                logger.debug(f"[API] Raw response: {order}")
                return order
        except Exception as e:
            logger.error(f"[API] Failed to place limit order for {symbol}: {e}")
            return None

    async def create_market_order(self, symbol, side, amount):
        try:
            async with self.throttle:
                market = self.exchange.market(symbol)
                precision_amount = market['precision']['amount']
                formatted_amount = float(f"{amount:.{precision_amount}f}")
                logger.debug(f"[API] Market order → {side.upper()} {symbol} qty={formatted_amount}")
                return await self.exchange.create_order(symbol, "market", side, formatted_amount)
        except Exception as e:
            logger.error(f"[API] Failed to place market order for {symbol}: {e}")
            return None

    async def close(self):
        try:
            await self.exchange.close()
        except Exception as e:
            logger.warning(f"[API] Failed to close exchange cleanly: {e}")