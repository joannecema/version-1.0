import time
import logging

log = logging.getLogger("PositionTracker")


class PositionTracker:
    def __init__(self, config, api):
        self.config = config
        self.api = api
        self.positions = {}  # symbol -> position dict

    def record_entry(self, symbol, side, size, price):
        self.positions[symbol] = {
            "symbol": symbol,
            "side": side,
            "size": size,
            "entry_price": price,
            "entry_time": time.time()
        }
        log.info(f"[TRACKER] 🟢 Entry recorded: {symbol} {side} size={size} @ {price}")

    def record_exit(self, symbol, exit_price=None):
        position = self.positions.pop(symbol, None)
        if not position:
            log.warning(f"[TRACKER] ⚠️ Tried to exit unknown position for {symbol}")
            return
        roi = self._calculate_roi(position["entry_price"], exit_price, position["side"])
        log.info(
            f"[TRACKER] 🔴 Exit {symbol} side={position['side']} size={position['size']} "
            f"@ {exit_price} | ROI={roi:.4f}"
        )

    def get_open_position(self, symbol):
        return self.positions.get(symbol)

    def has_open_position(self, symbol):
        return symbol in self.positions

    def _calculate_roi(self, entry_price, exit_price, side):
        if not entry_price or not exit_price:
            return 0.0
        if side == "long":
            return (exit_price - entry_price) / entry_price
        else:
            return (entry_price - exit_price) / entry_price

    async def evaluate_open_positions(self):
        now = time.time()
        for symbol, pos in list(self.positions.items()):
            current_price = await self.get_price(symbol)
            if not current_price:
                log.warning(f"[TRACKER] ⚠️ Skipping evaluation for {symbol} — price unavailable")
                continue

            roi = self._calculate_roi(pos["entry_price"], current_price, pos["side"])
            age = (now - pos["entry_time"]) / 60
            log.debug(f"[TRACKER] 📊 Eval {symbol} ROI={roi:.4f} Age={age:.1f}m")

            # Exit if loss exceeds threshold
            if roi < -self.config.get("max_loss_pct", 0.01):
                log.warning(f"[TRACKER] 📉 Exiting {symbol} — ROI {roi:.4f} below max loss limit")
                await self.api.create_market_order(symbol, "sell", pos["size"])
                self.record_exit(symbol, current_price)

            # Exit if trade is idle and hasn't moved
            elif age >= self.config.get("idle_exit_minutes", 3):
                roi_min = self.config.get("min_roi_idle_exit", 0.0001)
                if abs(roi) < roi_min:
                    log.warning(f"[TRACKER] 💤 Idle exit for {symbol} — ROI={roi:.4f} < {roi_min}")
                    await self.api.create_market_order(symbol, "sell", pos["size"])
                    self.record_exit(symbol, current_price)

    async def get_available_usdt(self):
        try:
            balance = await self.api.exchange.fetch_balance()
            usdt = balance.get('USDT', {}).get('free', 0)
            return float(usdt)
        except Exception as e:
            log.error(f"[TRACKER] ❌ Failed to fetch USDT balance: {e}")
            return 0

    async def get_price(self, symbol):
        try:
            ticker = await self.api.get_ticker(symbol)
            return ticker['last'] if ticker and 'last' in ticker else None
        except Exception as e:
            log.error(f"[TRACKER] ❌ Failed to fetch ticker for {symbol}: {e}")
            return None
