import asyncio
import random
import time
import ccxt.pro as ccxtpro
from functools import wraps
from typing import List, Dict


def retry(fn):
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        last_exc = None
        for i in range(5):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                last_exc = e
                await asyncio.sleep((2**i) + random.random())
        raise last_exc
    return wrapped


class ApiHandler:
    def __init__(self, api_key, api_secret, cfg):
        self.cfg = cfg
        self.exchange = ccxtpro.phemex({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        })

    @retry
    async def watch_ohlcv(self, symbol, timeframe, limit):
        return await self.exchange.watch_ohlcv(symbol, timeframe, limit)

    @retry
    async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, params=None):
        return await self.exchange.fetch_ohlcv(symbol, timeframe, since, limit, params or {})

    async def get_ohlcv(self, symbol, timeframe, limit):
        try:
            return await self.watch_ohlcv(symbol, timeframe, limit)
        except Exception:
            await asyncio.sleep(0.1)
            to_ts = int(time.time() * 1000)
            seconds = self.exchange.parse_timeframe(timeframe)
            from_ts = to_ts - limit * seconds * 1000
            return await self.fetch_ohlcv(
                symbol,
                timeframe,
                since=from_ts,
                limit=limit,
                params={
                    "start": from_ts,
                    "end": to_ts,
                },
            )

    @retry
    async def watch_ticker(self, symbol):
        return await self.exchange.watch_ticker(symbol)

    @retry
    async def fetch_ticker(self, symbol: str):
        return await self.exchange.fetch_ticker(symbol)

    async def get_ticker(self, symbol: str):
        """
        Try WebSocket ticker first; fallback to REST if WS fails.
        """
        try:
            return await self.watch_ticker(symbol)
        except Exception as e:
            print(f"[API] ⚠️ WebSocket ticker failed for {symbol}, fallback to REST: {e}")
            return await self.fetch_ticker(symbol)

    @retry
    async def fetch_tickers(self, symbols: List[str]) -> Dict[str, dict]:
        """
        Workaround for Phemex not supporting fetch_tickers().
        Fetch each symbol individually using WebSocket + REST fallback.
        """
        results = {}
        for symbol in symbols:
            try:
                ticker = await self.get_ticker(symbol)
                results[symbol] = ticker
            except Exception as e:
                print(f"[API] ❌ Failed to fetch ticker for {symbol}: {e}")
        return results

    @retry
    async def fetch_order_book(self, symbol, limit=5):
        try:
            return await self.exchange.watch_order_book(symbol, limit)
        except Exception as e:
            print(f"[API] ⚠️ WebSocket order book failed for {symbol}, no fallback available: {e}")
            raise e

    async def create_limit_order(self, symbol, side, amount, price, params):
        return await self.exchange.create_order(symbol, "limit", side, amount, price, params)

    async def create_market_order(self, symbol, side, amount):
        return await self.exchange.create_order(symbol, "market", side, amount)

    async def close(self):
        await self.exchange.close()