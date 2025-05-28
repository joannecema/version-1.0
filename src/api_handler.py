import asyncio
import random
import ccxt.pro as ccxtpro
from functools import wraps

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
        # markets loaded in bot.py

    @retry
    async def watch_ohlcv(self, symbol, timeframe, limit):
        return await self.exchange.watch_ohlcv(symbol, timeframe, limit)

    @retry
    async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        return await self.exchange.fetch_ohlcv(symbol, timeframe, since, limit)

    async def get_ohlcv(self, symbol, timeframe, limit):
        """
        Unified OHLCV fetch: WS first, then REST.
        """
        try:
            return await self.watch_ohlcv(symbol, timeframe, limit)
        except Exception:
            await asyncio.sleep(0.1)
            return await self.fetch_ohlcv(symbol, timeframe, None, limit)

    @retry
    async def watch_ticker(self, symbol):
        return await self.exchange.watch_ticker(symbol)

    @retry
    async def fetch_tickers(self):
        return await self.exchange.fetch_tickers()

    @retry
    async def fetch_order_book(self, symbol, limit=5):
        return await self.exchange.watch_order_book(symbol, limit)

    async def create_limit_order(self, symbol, side, amount, price, params):
        return await self.exchange.create_order(symbol, "limit", side, amount, price, params)

    async def create_market_order(self, symbol, side, amount):
        return await self.exchange.create_order(symbol, "market", side, amount)
