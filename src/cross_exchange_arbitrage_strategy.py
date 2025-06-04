import logging
import ccxt.pro as ccxtpro
import asyncio

log = logging.getLogger("CrossExchangeArbitrage")

class CrossExchangeArbitrageStrategy:
    def __init__(self, api, tracker, executor, cfg):
        self.api = api
        self.phemex = api.exchange
        self.tracker = tracker
        self.exec = executor
        self.cfg = cfg

        self.pairs = cfg.get("cross_ex_pairs", [])
        self.thresh = cfg.get("arb_threshold_pct", 0.002)  # e.g. 0.2%

        self.binance = ccxtpro.binance({
            "enableRateLimit": True,
        })

        self._markets_loaded = False

    async def initialize(self):
        try:
            await self.phemex.load_markets()
            await self.binance.load_markets()
            self._markets_loaded = True
            log.info("[ARB] ✅ Market data loaded for Phemex and Binance.")
        except Exception as e:
            log.error(f"[ARB] ❌ Failed to load markets: {e}")
            self._markets_loaded = False

    async def check_and_trade(self, _=None):
        if not self._markets_loaded:
            await self.initialize()
            if not self._markets_loaded:
                return

        for symbol, _ in self.pairs:
            try:
                # Get Phemex market ID (normalized)
                market_id = self.api.get_market_id(symbol)
                p_tick = await self.api.get_ticker(symbol)
                if not p_tick:
                    raise ValueError("Phemex ticker returned None")
            except Exception as e:
                log.error(f"[ARB] ❌ Failed to fetch Phemex ticker for {symbol}: {e}")
                continue

            try:
                # Prefer Binance WebSocket, fallback to REST
                b_tick = await self.binance.watch_ticker(symbol)
            except Exception as e:
                log.warning(f"[ARB] ⚠️ WebSocket failed for {symbol}, falling back to REST: {e}")
                try:
                    b_tick = await self.binance.fetch_ticker(symbol)
                except Exception as e2:
                    log.error(f"[ARB] ❌ Binance REST ticker failed for {symbol}: {e2}")
                    continue

            # Extract bid/ask
            p_bid = p_tick.get("bid")
            b_ask = b_tick.get("ask")

            if not p_bid or not b_ask or b_ask <= 0:
                log.warning(f"[ARB] ⚠️ Invalid price data for {symbol}: Phemex bid={p_bid}, Binance ask={b_ask}")
                continue

            spread_pct = (p_bid - b_ask) / b_ask
            log.debug(f"[ARB] {symbol} spread: {spread_pct:.4%}")

            if spread_pct >= self.thresh:
                try:
                    usdt_balance = await self.tracker.get_available_usdt()
                    risk_pct = self.cfg.get("risk_pct", 0.1)
                    qty = round((usdt_balance * risk_pct) / b_ask, 6)

                    await self.exec.market_cross_order("binance", symbol, "buy", qty)
                    await self.exec.market_cross_order("phemex", market_id, "sell", qty)

                    log.info(f"[ARB] ✅ Arbitrage executed: {symbol} | QTY={qty:.4f} | Spread={spread_pct:.4%}")
                except Exception as e:
                    log.error(f"[ARB] ❌ Order execution failed for {symbol}: {e}")
            else:
                log.debug(f"[ARB] ❌ No arb: spread={spread_pct:.4%} < threshold={self.thresh:.4%}")
