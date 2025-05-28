import logging
from decimal import Decimal, ROUND_DOWN

log = logging.getLogger("TradeExecutor")

class TradeExecutor:
    def __init__(self, api, tracker, config):
        self.api = api
        self.tracker = tracker
        self.config = config

    async def enter_long(self, symbol, entry_price=None):
        try:
            usdt_balance = await self.tracker.get_available_usdt()
            if usdt_balance <= 0:
                log.warning(f"[EXECUTOR] ⚠️ Skipped LONG — No USDT balance for {symbol}")
                return

            price = entry_price or await self.tracker.get_price(symbol)
            size = self._calculate_trade_size(usdt_balance, price)
            if size <= 0:
                log.warning(f"[EXECUTOR] ⚠️ Skipped LONG — Invalid size for {symbol}")
                return

            result = await self.api.create_market_order(symbol, "buy", size)
            if result:
                log.info(f"[EXECUTOR] ✅ LONG {symbol} size={size}")
                self.tracker.record_entry(symbol, "long", size, result.get("price") or price)
            else:
                log.error(f"[EXECUTOR] ❌ LONG FAILED for {symbol}")
        except Exception as e:
            log.error(f"[EXECUTOR] ❌ Exception during LONG for {symbol}: {e}")

    async def enter_short(self, symbol, entry_price=None):
        log.warning(f"[EXECUTOR] ⚠️ SHORT not implemented for SPOT market — skipping {symbol}")

    async def exit_position(self, symbol, exit_price=None):
        try:
            position = self.tracker.get_open_position(symbol)
            if not position:
                log.info(f"[EXECUTOR] ⏹ No position to exit for {symbol}")
                return

            side = "sell" if position["side"] == "long" else "buy"
            result = await self.api.create_market_order(symbol, side, position["size"])
            if result:
                log.info(f"[EXECUTOR] ✅ EXIT {symbol} side={side} size={position['size']}")
                self.tracker.record_exit(symbol, result.get("price") or exit_price)
            else:
                log.error(f"[EXECUTOR] ❌ EXIT FAILED for {symbol} — retry may be needed")
        except Exception as e:
            log.error(f"[EXECUTOR] ❌ Exception during EXIT for {symbol}: {e}")

    def _calculate_trade_size(self, usdt_balance, price):
        if not price or price <= 0:
            return 0

        try:
            percent = self.config.get("trade_allocation_pct", 0.1)
            capital = usdt_balance * percent
            size = Decimal(capital / price).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
            return float(size)
        except Exception as e:
            log.error(f"[EXECUTOR] ❌ Error calculating trade size: {e}")
            return 0