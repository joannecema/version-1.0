import asyncio
import logging
import ccxt.pro as ccxtpro
import queue

class TradeExecutor:
    def __init__(self, api, tracker, cfg, md_queue=None):
        self.api = api
        self.tracker = tracker
        self.cfg = cfg
        self.md_queue = md_queue
        self.binance = ccxtpro.binance({"enableRateLimit": True})

    async def route_order(self, symbol, side, amount):
        p_b, p_a = None, None
        try:
            while True:
                sym, b, a = self.md_queue.get_nowait()
                if sym == symbol:
                    p_b, p_a = b, a
                    break
        except queue.Empty:
            try:
                tick = await self.api.get_ticker(symbol)  # ✅ uses safe fallback
                p_b, p_a = tick.get("bid"), tick.get("ask")
            except Exception as e:
                logging.error(f"[ROUTER] Failed to get Phemex ticker for {symbol}: {e}")
                return

        try:
            b_tick = await self.binance.watch_ticker(symbol)
            bin_b, bin_a = b_tick.get("bid"), b_tick.get("ask")
        except Exception as e:
            logging.error(f"[ROUTER] Failed to get Binance ticker for {symbol}: {e}")
            return

        if None in (p_b, p_a, bin_b, bin_a):
            logging.warning(f"[ROUTER] Incomplete ticker data for {symbol}")
            return

        if side == "buy":
            best, venue = min((p_a, "phemex"), (bin_a, "binance"))
        else:
            best, venue = max((p_b, "phemex"), (bin_b, "binance"))

        logging.info(f"[ROUTER] {side.upper()} {symbol}@{best:.4f} via {venue}")
        try:
            if venue == "phemex":
                return await self.api.create_limit_order(symbol, side, amount, best, {"timeInForce": "IOC"})
            else:
                return await self.binance.create_order(symbol, "limit", side, amount, best, {"timeInForce": "IOC"})
        except Exception as e:
            logging.error(f"[ROUTER] Order placement failed for {symbol} on {venue}: {e}")

    async def enter(self, symbol, side, amount, tp, sl):
        logging.info(f"[EXEC] ENTRY {side.upper()} {symbol} qty={amount:.6f}")
        await self.route_order(symbol, side, amount)
        self.tracker.record_entry(symbol, side, amount, tp, tp, sl)  # assumes entry_price == tp

    async def exit(self, symbol, exit_price=None):
        try:
            pos = self.tracker.open_positions[symbol]
        except KeyError:
            logging.warning(f"[EXEC] No open position to exit for {symbol}")
            return

        side = "sell" if pos["side"] == "buy" else "buy"
        try:
            price = exit_price or (await self.api.get_ticker(symbol)).get("last")
        except Exception as e:
            logging.error(f"[EXEC] Failed to fetch exit price for {symbol}: {e}")
            return

        logging.info(f"[EXEC] EXIT {side.upper()} {symbol} @ {price:.2f}")
        await self.route_order(symbol, side, pos["amount"])
        self.tracker.record_exit(symbol, price)

    async def market_cross_order(self, exchange_name, symbol, side, amount):
        try:
            if exchange_name == "phemex":
                await self.api.exchange.create_order(symbol, "market", side, amount)
            else:
                await self.binance.create_order(symbol, "market", side, amount)
        except Exception as e:
            logging.error(f"[EXEC] Market order failed on {exchange_name} for {symbol}: {e}")