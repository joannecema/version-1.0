# src/api_handler.py

import asyncio
import logging
import random
import time
from functools import wraps
from typing import List, Dict, Optional

import ccxt.pro as ccxtpro

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
        self.throttle = asyncio.Semaphore(cfg.get("max_concurrent_requests", 3))

        self.exchange = ccxtpro.phemex({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"}
        })

        self.market_map = {}

    async def load_markets(self):
        try:
            await self.exchange.load_markets()
            self.market_map = {
                symbol: market["id"]
                for symbol, market in self.exchange.markets.items()
                if market.get("spot", True)
            }
            logger.info(f"[API] ✅ Loaded {len(self.market_map)} spot symbols from Phemex")
        except Exception as e:
            logger.error(f"[API] ❌ Failed to load markets: {e}")
            raise

    def get_market_id(self, symbol: str) -> str:
        if symbol in self.market_map:
            return self.market_map[symbol]
        try:
            market = self.exchange.market(symbol)
            return market["id"]
        except Exception as e:
            logger.warning(f"[API] ⚠️ Symbol resolution fallback for {symbol}: {e}")
            return symbol.replace("/", "")

    @retry
    async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, params=None):
        async with self.throttle:
            try:
                market_id = self.get_market_id(symbol)
                query = params.copy() if params else {}
                query["to"] = int(time.time() * 1000)
                return await self.exchange.fetch_ohlcv(market_id, timeframe, since, limit, query)
            except Exception as e:
                logger.error(f"[API] fetch_ohlcv failed for {symbol}: {e}")
                return []

    async def get_ohlcv(self, symbol, timeframe, limit):
        try:
            if not self.disable_ws:
                return await self.watch_ohlcv(symbol, timeframe, limit)
        except Exception as e:
            logger.warning(f"[API] watch_ohlcv failed for {symbol}, falling back to REST: {e}")
        await asyncio.sleep(0.25)
        to_ts = int(time.time() * 1000)
        seconds = self.exchange.parse_timeframe(timeframe)
        from_ts = to_ts - limit * seconds * 1000
        return await self.fetch_ohlcv(symbol, timeframe, since=from_ts, limit=limit)

    @retry
    async def watch_ohlcv(self, symbol, timeframe, limit):
        if self.disable_ws:
            raise RuntimeError("WebSocket is disabled by config.")
        market_id = self.get_market_id(symbol)
        return await self.exchange.watch_ohlcv(market_id, timeframe, limit)

    @retry
    async def fetch_ticker(self, symbol: str):
        async with self.throttle:
            market_id = self.get_market_id(symbol)
            return await self.exchange.fetch_ticker(market_id)

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
    async def watch_ticker(self, symbol):
        if self.disable_ws:
            raise RuntimeError("WebSocket is disabled by config.")
        market_id = self.get_market_id(symbol)
        return await self.exchange.watch_ticker(market_id)

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
        market_id = self.get_market_id(symbol)
        if self.disable_ws:
            logger.warning(f"[API] WebSocket is disabled — using REST for {symbol}")
            try:
                async with self.throttle:
                    return await self.exchange.fetch_order_book(market_id, limit=limit)
            except Exception as e:
                logger.error(f"[API] REST fetch_order_book failed for {symbol}: {e}")
                return None
        else:
            try:
                return await self.exchange.watch_order_book(market_id, limit)
            except Exception as e:
                logger.warning(f"[API] WebSocket order book failed for {symbol}, fallback to REST: {e}")
                try:
                    async with self.throttle:
                        return await self.exchange.fetch_order_book(market_id, limit=limit)
                except Exception as e2:
                    logger.error(f"[API] REST fallback also failed for {symbol}: {e2}")
                    return None

    async def create_market_order(self, symbol, side, amount):
        try:
            async with self.throttle:
                market_id = self.get_market_id(symbol)
                market = self.exchange.market(symbol)
                prec_amt = market["precision"].get("amount", 8)
                amt = round(float(amount), prec_amt)
                logger.debug(f"[API] Market order → {side.upper()} {symbol} qty={amt}")
                return await self.exchange.create_order(market_id, "market", side, amt)
        except Exception as e:
            logger.error(f"[API] Failed to place market order for {symbol}: {e}")
            return None

    async def create_limit_order(self, symbol, side, amount, price, params):
        try:
            async with self.throttle:
                market_id = self.get_market_id(symbol)
                market = self.exchange.market(symbol)
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

                logger.debug(f"[API] Payload → {side.upper()} {symbol} qty={amt} price={prc} PARAMS={clean_params}")
                order = await self.exchange.create_order(market_id, "limit", side, amt, prc, clean_params)
                logger.info(f"[API] Order placed → ID={order.get('id')} STATUS={order.get('status')}")
                logger.debug(f"[API] Raw response: {order}")
                return order
        except Exception as e:
            logger.error(f"[API] Failed to place limit order for {symbol}: {e}")
            return None

    @retry
    async def fetch_open_orders(self, symbol=None):
        try:
            async with self.throttle:
                if symbol:
                    market_id = self.get_market_id(symbol)
                    return await self.exchange.fetch_open_orders(market_id)
                else:
                    return await self.exchange.fetch_open_orders()
        except Exception as e:
            logger.error(f"[API] Failed to fetch open orders for {symbol or 'ALL'}: {e}")
            return []

    async def log_open_orders(self, symbol=None):
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

    async def close(self):
        try:
            await self.exchange.close()
        except Exception as e:
            logger.warning(f"[API] Failed to close exchange cleanly: {e}")