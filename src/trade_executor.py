import asyncio
import logging
import ccxt.pro as ccxtpro
import queue
import os

class TradeExecutor:
    def __init__(self, api, tracker, cfg, md_queue=None):
        self.api = api
        self.tracker = tracker
        self.cfg = cfg
        self.md_queue = md_queue

        self.US_DEPLOYMENT = os.getenv("DEPLOY_REGION", "").lower() == "us"
        self.binance_enabled = cfg.get("enable_binance", True) and not self.US_DEPLOYMENT
        self.binance = None

        if self.binance_enabled:
            try:
                self.binance = ccxtpro.binance({"enableRateLimit": True})
                asyncio.create_task(self._check_binance_block())
            except Exception as e:
                logging.warning(f"[BINANCE] Initialization failed: {e}")
                self.binance_enabled = False

    async def _check_binance_block(self):
        try:
            await self.binance.load_markets()
        except Exception as e:
            if "restricted location" in str(e).lower() or "451" in str(e):
                logging.warning(f"[BINANCE] Access blocked in region: {e}")
                self.binance_enabled = False
        finally:
            try:
                await self.binance.close()
            except Exception:
                pass

    async def route_order(self, symbol, side, amount):
        p_b, p_a = None, None

        if self.md_queue:
            try:
                while True:
                    sym, b, a = self.md_queue.get_nowait()
                    if sym == symbol:
                        p_b, p_a = b, a
                        break
            except queue.Empty:
                pass
            except Exception as e:
                logging.warning(f"[ROUTER] Market data queue failed for {symbol}: {e}")

        if p_b is None or p_a is None:
            try:
                tick = await self.api.get_ticker(symbol)
                if not tick:
                    logging.error(f"[ROUTER] Phemex ticker for {symbol} is None")
                    return None
                p_b, p_a = tick.get("bid"), tick.get("ask")
            except Exception as e:
                logging.error(f"[ROUTER] Failed to get Phemex ticker for {symbol}: {e}")
                return None

        bin_b = bin_a = None
        if self.binance_enabled and self.binance:
            try:
                b_tick = await self.binance.fetch_ticker(symbol)
                bin_b, bin_a = b_tick.get("bid"), b_tick.get("ask")
            except Exception as e:
                logging.warning(f"[ROUTER] Binance ticker fetch failed for {symbol}: {e}")
                self.binance_enabled = False

        if side == "buy":
            if bin_a is not None and self.binance_enabled:
                best, venue = min((p_a, "phemex"), (bin_a, "binance"))
            else:
                best, venue = p_a, "phemex"
        else:
            if bin_b is not None and self.binance_enabled:
                best, venue = max((p_b, "phemex"), (bin_b, "binance"))
            else:
                best, venue = p_b, "phemex"

        if best is None:
            logging.error(f"[ROUTER] No valid price found for {symbol}")
            return None

        logging.info(f"[ROUTER] {side.upper()} {symbol}@{best:.4f} via {venue}")
        try:
            if venue == "phemex":
                return await self.api.create_limit_order(symbol, side, amount, best, {"timeInForce": "IOC"})
            elif venue == "binance" and self.binance_enabled:
                return await self.binance.create_order(symbol, "limit", side, amount, best, {"timeInForce": "IOC"})
        except Exception as e:
            logging.error(f"[ROUTER] Order placement failed for {symbol} on {venue}: {e}")
        return None

    async def enter(self, symbol, side, amount, tp, sl):
        logging.info(f"[EXEC] ENTRY {side.upper()} {symbol} qty={amount:.6f}")
        result = await self.route_order(symbol, side, amount)
        if result:
            self.tracker.record_entry(symbol, side, amount, tp, tp, sl)

    async def exit(self, symbol, exit_price=None):
        try:
            pos = self.tracker.open_positions[symbol]
        except KeyError:
            logging.warning(f"[EXEC] No open position to exit for {symbol}")
            return

        side = "sell" if pos["side"] == "buy" else "buy"
        try:
            ticker = await self.api.get_ticker(symbol)
            if not ticker:
                logging.warning(f"[EXEC] No ticker data available to exit {symbol}")
                return
            price = exit_price or ticker.get("last")
        except Exception as e:
            logging.error(f"[EXEC] Failed to fetch exit price for {symbol}: {e}")
            return

        if price is None:
            logging.warning(f"[EXEC] Exit price is None for {symbol}")
            return

        logging.info(f"[EXEC] EXIT {side.upper()} {symbol} @ {price:.2f}")
        result = await self.route_order(symbol, side, pos["amount"])
        if result:
            self.tracker.record_exit(symbol, price)

    async def market_cross_order(self, exchange_name, symbol, side, amount):
        try:
            if exchange_name == "phemex":
                await self.api.exchange.create_order(symbol, "market", side, amount)
            elif exchange_name == "binance" and self.binance_enabled:
                await self.binance.create_order(symbol, "market", side, amount)
            else:
                logging.warning(f"[EXEC] Market order skipped — exchange '{exchange_name}' unavailable.")
        except Exception as e:
            logging.error(f"[EXEC] Market order failed on {exchange_name} for {symbol}: {e}")