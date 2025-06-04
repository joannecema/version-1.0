import ccxt.async_support as ccxt
import logging
import asyncio
from typing import List, Optional, Dict

log = logging.getLogger("API")

STABLECOINS = {"USDT", "USDC", "TUSD", "BUSD", "DAI", "USDP"}

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

    def _is_valid_symbol(self, symbol: str) -> bool:
        base, quote = symbol.split("/")
        return quote.upper() in STABLECOINS and base.upper() not in STABLECOINS

    async def get_top_symbols(self, count: int = 10, exclude_stable: bool = True) -> List[str]:
        try:
            if not self.markets:
                await self.load_markets()

            symbols = [s for s in self.markets if (not exclude_stable or self._is_valid_symbol(s))]
            volumes = []

            for symbol in symbols:
                try:
                    ticker = await self.exchange.fetch_ticker(symbol)
                    volume = ticker.get("quoteVolume")
                    if volume and isinstance(volume, (int, float)):
                        volumes.append((symbol, volume))
                except Exception:
                    continue

            sorted_volumes = sorted(volumes, key=lambda x: x[1], reverse=True)
            top = [s for s, _ in sorted_volumes[:count]]

            log.info(f"[API] 📈 Top {count} symbols: {top}")
            return top if top else ["BTC/USDT"]

        except Exception as e:
            log.error(f"[API] ❌ Failed to fetch top symbols: {e}")
            return ["BTC/USDT"]

    async def get_balance(self, currency: str = "USDT") -> float:
        try:
            balance = await self.exchange.fetch_balance()
            return balance.get(currency, {}).get("free", 0)
        except Exception as e:
            log.error(f"[API] ❌ Failed to fetch balance: {e}")
            return 0

    async def fetch_ticker(self, symbol: str) -> Optional[float]:
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            return ticker.get("last")
        except Exception as e:
            log.error(f"[API] ❌ Failed to fetch ticker for {symbol}: {e}")
            return None

    async def fetch_ohlcv(self, symbol: str, timeframe: str = '1m', limit: int = 20) -> List[List[float]]:
        for attempt in range(3):
            try:
                if not self.markets or symbol not in self.symbol_map:
                    await self.load_markets()

                market_id = self.symbol_map.get(symbol)
                if not market_id:
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
                return await self.exchange.fetch_ohlcv(market_id, timeframe, since=since)

            except ccxt.RateLimitExceeded:
                wait = (attempt + 1) * 2
                log.warning(f"[API] ⏳ Rate limit hit for {symbol}, retrying in {wait}s...")
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
        try:
            await self.exchange.close()
            log.info("[API] 🔌 Exchange connection closed.")
        except Exception as e:
            log.error(f"[API] ❌ Failed to close exchange: {e}")
