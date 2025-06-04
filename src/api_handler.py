import ccxt.async_support as ccxt
import logging
import asyncio
from typing import List, Optional, Dict

log = logging.getLogger("API")

class ApiHandler:
    def __init__(self, api_key: str, api_secret: str, config: Optional[Dict] = None):
        self.config = config or {}
        self.exchange = ccxt.phemex({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
            }
        })
        self.markets = {}
        self.symbol_map = {}  # Maps "BTC/USDT" → "BTCUSDT"

    async def load_markets(self):
        try:
            self.markets = await self.exchange.load_markets()
            self.symbol_map = {
                market['symbol']: market['id'] for market in self.markets.values()
            }
            log.info(f"[API] ✅ Loaded {len(self.markets)} markets from Phemex")
        except Exception as e:
            log.error(f"[API] ❌ Failed to load markets: {e}")

    async def get_balance(self, currency: str = "USDT") -> float:
        try:
            balance = await self.exchange.fetch_balance()
            free_balance = balance.get(currency, {}).get('free', 0)
            log.debug(f"[API] Free {currency} balance: {free_balance}")
            return free_balance
        except Exception as e:
            log.error(f"[API] ❌ Failed to fetch balance: {e}")
            return 0

    async def fetch_ticker(self, symbol: str) -> Optional[float]:
        try:
            return (await self.exchange.fetch_ticker(symbol)).get("last")
        except Exception as e:
            log.error(f"[API] ❌ Failed to fetch ticker for {symbol}: {e}")
            return None

    async def fetch_ohlcv(self, symbol: str, timeframe: str = '1m', limit: int = 20) -> List[List[float]]:
        for attempt in range(3):
            try:
                if not self.markets or symbol not in self.symbol_map:
                    log.debug(f"[API] Symbol {symbol} not in symbol_map — reloading markets")
                    await self.load_markets()

                market_id = self.symbol_map.get(symbol)
                if not market_id:
                    # Fallback to find matching symbol
                    for market in self.markets.values():
                        if market['symbol'].replace("/", "").upper() == symbol.replace("/", "").upper():
                            market_id = market['id']
                            self.symbol_map[symbol] = market_id
                            log.warning(f"[API] ⚠️ Fallback matched {symbol} → {market_id}")
                            break

                if not market_id:
                    log.error(f"[API] ❌ No market ID found for {symbol}")
                    return []

                since = self.exchange.milliseconds() - self.exchange.parse_timeframe(timeframe) * 1000 * limit

                log.debug(f"[API] Fetching OHLCV: {symbol} → {market_id} | since={since}")
                return await self.exchange.fetch_ohlcv(
                    market_id,
                    timeframe=timeframe,
                    since=since
                )
            except ccxt.RateLimitExceeded:
                wait = (attempt + 1) * 2
                log.warning(f"[API] ⏳ Rate limit hit for {symbol}. Retrying in {wait}s...")
                await asyncio.sleep(wait)
            except Exception as e:
                log.error(f"[API] ❌ fetch_ohlcv failed for {symbol}: {e}")
                await asyncio.sleep(1)
        return []

    async def get_ohlcv(self, symbol: str, timeframe: str = '1m', limit: int = 20) -> List[List[float]]:
        return await self.fetch_ohlcv(symbol, timeframe, limit)

    async def create_market_order(self, symbol: str, side: str, amount: float) -> Optional[Dict]:
        try:
            order = await self.exchange.create_market_order(symbol, side, amount)
            log.info(f"[API] ✅ Placed {side.upper()} order for {symbol}: size={amount}")
            return order
        except Exception as e:
            log.error(f"[API] ❌ Failed to place {side.upper()} order for {symbol}: {e}")
            return None

    async def close(self):
        await self.exchange.close()
