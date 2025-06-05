# cross_exchange_arbitrage_strategy.py
import logging
import asyncio
import os
import ccxt.async_support as ccxt

log = logging.getLogger("CrossExchangeArbitrage")

class CrossExchangeArbitrageStrategy:
    def __init__(self, api, tracker, executor, cfg):
        self.api = api
        self.phemex = api.exchange
        self.tracker = tracker
        self.exec = executor
        self.cfg = cfg

        self.pairs = cfg.get("cross_ex_pairs", [])
        self.thresh = cfg.get("arb_threshold_pct", 0.002)

        self.binance = ccxt.binance({
            "apiKey": os.getenv("BINANCE_API_KEY"),
            "secret": os.getenv("BINANCE_API_SECRET"),
            "enableRateLimit": True,
        })

        self._markets_loaded = False

    async def initialize(self):
        try:
            await asyncio.gather(
                self.phemex.load_markets(),
                self.binance.load_markets()
            )
            self._markets_loaded = True
        except Exception as e:
            log.error(f"[ARB] Market load failed: {e}")

    async def check_and_trade(self):
        if not self._markets_loaded:
            await self.initialize()
            if not self._markets_loaded:
                return

        for symbol, _ in self.pairs:
            try:
                market_id = self.api.get_market_id(symbol)
                p_tick, b_tick = await asyncio.gather(
                    self.api.fetch_ticker(symbol),
                    self.binance.fetch_ticker(symbol)
                )
                
                if not p_tick or not b_tick:
                    continue
                    
                p_bid = p_tick.get("bid", 0)
                b_ask = b_tick.get("ask", 0)
                
                if p_bid <= 0 or b_ask <= 0:
                    continue

                spread_pct = (p_bid - b_ask) / b_ask
                log.debug(f"[ARB] {symbol} spread: {spread_pct:.4%}")

                if spread_pct >= self.thresh:
                    usdt_balance = self.tracker.get_available_usdt()
                    risk_pct = self.cfg.get("risk_pct", 0.1)
                    qty = (usdt_balance * risk_pct) / b_ask
                    
                    # Execute in parallel
                    await asyncio.gather(
                        self.exec.execute_order("binance", symbol, "buy", qty),
                        self.exec.execute_order("phemex", market_id, "sell", qty)
                    )
                    log.info(f"[ARB] Executed arb: {symbol} | QTY={qty:.4f}")

            except Exception as e:
                log.error(f"[ARB] Error for {symbol}: {e}")
