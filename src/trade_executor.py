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

    def sanitize_order_params(self, params):
        clean = {}
        for k, v in (params or {}).items():
            try:
                if isinstance(v, str) or isinstance(v, float):
                    clean[k] = int(float(v))
                else:
                    clean[k] = v
            except Exception as e:
                logging.warning(f"[SANITIZE] Could not cast param {k}={v}: {e}")
                clean[k] = v
        return clean

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
            best, venue = (p_a, "phemex")
            if bin_a is not None and self.binance_enabled:
                best, venue = min((p_a, "phemex"), (bin_a, "binance"))
        else:
            best, venue = (p_b, "phemex")
            if bin_b is not None and self.binance_enabled:
                best, venue = max((p_b, "phemex"), (bin_b, "binance"))

        if best is None:
            logging.error(f"[ROUTER] No valid price found for {symbol}")
            return None

        precision = self.cfg.get("price_precision", 4)
        price = round(best, precision)
        symbol_id = self.api.exchange.market_id(symbol)
        logging.info(f"[ROUTER] {side.upper()} {symbol}@{price} via {venue}")
        logging.debug(f"[PAYLOAD] symbol_id={symbol_id}, side={side}, amount={amount}, price={price}, timeInForce=IOC")

        try:
            params = self.sanitize_order_params({"timeInForce": "IOC"})

            if venue == "phemex":
                result = await self.api.exchange.create_order(
                    symbol_id, "limit", side, amount, price, params
                )
            elif venue == "binance" and self.binance_enabled:
                result = await self.binance.create_order(
                    symbol, "limit", side, amount, price, params
                )
            else:
                result = None

            if result:
                logging.debug(f"[ROUTER] Order result: {result}")
            else:
                logging.error(f"[ROUTER] No result returned for {symbol} order on {venue}")
            return result

        except Exception as e:
            logging.error(f"[ROUTER] Order placement failed for {symbol} on {venue}: {e}")
            return None

    async def enter(self, symbol, side, amount, tp, sl):
        logging.info(f"[EXEC] ENTRY {side.upper()} {symbol} qty={amount:.6f}")
        result = await self.route_order(symbol, side, amount)
        if result:
            logging.info(f"[EXEC] Entry order placed for {symbol}: {result}")
            self.tracker.record_entry(symbol, side, amount, tp, tp, sl)
        else:
            logging.error(f"[EXEC] Entry order failed for {symbol}")

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
            logging.info(f"[EXEC] Exit order placed for {symbol}: {result}")
            self.tracker.record_exit(symbol, price)
        else:
            logging.error(f"[EXEC] Exit order failed for {symbol}")

    async def market_cross_order(self, exchange_name, symbol, side, amount):
        try:
            if exchange_name == "phemex":
                result = await self.api.exchange.create_order(symbol, "market", side, amount)
            elif exchange_name == "binance" and self.binance_enabled:
                result = await self.binance.create_order(symbol, "market", side, amount)
            else:
                logging.warning(f"[EXEC] Market order skipped — exchange '{exchange_name}' unavailable.")
                return
            logging.debug(f"[EXEC] Market order result on {exchange_name}: {result}")
        except Exception as e:
            logging.error(f"[EXEC] Market order failed on {exchange_name} for {symbol}: {e}")