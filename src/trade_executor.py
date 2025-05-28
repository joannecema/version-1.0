import asyncio, logging, ccxt.pro as ccxtpro, queue

class TradeExecutor:
    def __init__(self, api, tracker, cfg, md_queue=None):
        self.api, self.tracker, self.cfg, self.md_queue = api, tracker, cfg, md_queue
        self.binance = ccxtpro.binance({"enableRateLimit": True})

    async def route_order(self, symbol, side, amount):
        # get Phemex MD
        p_b, p_a = None, None
        try:
            while True:
                sym, b, a = self.md_queue.get_nowait()
                if sym == symbol:
                    p_b, p_a = b, a
                    break
        except queue.Empty:
            tick = await self.api.watch_ticker(symbol)
            p_b, p_a = tick["bid"], tick["ask"]

        # get Binance top book
        b_tick = await self.binance.watch_ticker(symbol)
        bin_b, bin_a = b_tick["bid"], b_tick["ask"]

        # choose best
        if side=="buy":
            best, ven = min((p_a,"phemex"),(bin_a,"binance"))
        else:
            best, ven = max((p_b,"phemex"),(bin_b,"binance"))
        logging.info(f"[ROUTER] {side.upper()} {symbol}@{best:.4f} via {ven}")
        if ven=="phemex":
            return await self.api.create_limit_order(symbol, side, amount, best, {"timeInForce":"IOC"})
        else:
            return await self.binance.create_order(symbol,"limit",side,amount,best,{"timeInForce":"IOC"})

    async def enter(self, symbol, side, amount, tp, sl):
        logging.info(f"[EXEC] ENTRY {side.upper()} {symbol} qty={amount:.6f}")
        await self.route_order(symbol, side, amount)
        self.tracker.record_entry(symbol, side, amount, tp, tp, sl)  # entry_price stored as tp for simplicity

    async def exit(self, symbol, exit_price=None):
        pos = self.tracker.open_positions[symbol]
        side = "sell" if pos["side"]=="buy" else "buy"
        price = exit_price or (await self.api.watch_ticker(symbol))["last"]
        logging.info(f"[EXEC] EXIT {side.upper()} {symbol} @ {price:.2f}")
        await self.route_order(symbol, side, pos["amount"])
        self.tracker.record_exit(symbol, price)

    async def market_cross_order(self, exchange_name, symbol, side, amount):
        if exchange_name=="phemex":
            await self.api.exchange.create_order(symbol,"market",side,amount)
        else:
            await self.binance.create_order(symbol,"market",side,amount)
