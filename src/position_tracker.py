# position_tracker.py
import logging
import time
import asyncio

log = logging.getLogger("PositionTracker")

class PositionTracker:
    def __init__(self, config, api):
        self.config = config
        self.api = api
        self.positions = {}
        self.trade_history = []
        self.daily_start_balance = None
        self.balance = 0.0
        self.last_sync = 0
        self.sync_interval = 60  # seconds
        
    async def sync(self):
        """Synchronize balance and positions with exchange"""
        current_time = time.time()
        if current_time - self.last_sync > self.sync_interval:
            await self._sync_balance()
            await self._sync_positions()
            self.last_sync = current_time
            
    async def _sync_balance(self):
        try:
            balance = await self.api.fetch_balance()
            self.balance = balance.get("USDT", {}).get("free", 0)
            if not self.daily_start_balance:
                self.daily_start_balance = self.balance
        except Exception as e:
            log.error("Balance sync failed: %s", e)
            
    async def _sync_positions(self):
        try:
            exchange_positions = await self.api.fetch_positions()
            
            # Remove positions not on exchange
            for symbol in list(self.positions.keys()):
                if symbol not in exchange_positions:
                    log.warning("Position missing on exchange: %s", symbol)
                    self.positions.pop(symbol)
                    
            # Add positions missing locally
            for symbol, pos in exchange_positions.items():
                if symbol not in self.positions:
                    log.info("Reconciling position: %s", symbol)
                    self.record_entry(
                        symbol,
                        pos["side"],
                        pos["size"],
                        pos["entry_price"],
                        "reconciled"
                    )
        except Exception as e:
            log.error("Position sync failed: %s", e)
            
    async def daily_pnl(self):
        await self.sync()
        if not self.daily_start_balance or self.daily_start_balance <= 0:
            return 0
        return (self.balance - self.daily_start_balance) / self.daily_start_balance
        
    def get_open_position(self, symbol):
        return self.positions.get(symbol)
        
    def record_entry(self, symbol, side, size, price, strategy):
        position = {
            "symbol": symbol,
            "side": side,
            "size": size,
            "entry_price": price,
            "entry_time": time.time(),
            "strategy": strategy,
            "stop_loss": self._calculate_stop_loss(side, price),
            "take_profit": self._calculate_take_profit(side, price),
            "liquidation_price": self._calculate_liquidation_price(side, price, size)
        }
        self.positions[symbol] = position
        log.info("Opened %s: %s %s @ %s", symbol, side.upper(), size, price)
        return position
        
    def record_exit(self, symbol, exit_price):
        position = self.positions.pop(symbol, None)
        if not position:
            return
            
        pnl = self._calculate_pnl(position, exit_price)
        position["exit_price"] = exit_price
        position["exit_time"] = time.time()
        position["pnl"] = pnl
        self.trade_history.append(position)
        log.info("Closed %s @ %s | PnL: %.4f", symbol, exit_price, pnl)
        return position
        
    def _calculate_stop_loss(self, side, entry_price):
        sl_pct = self.config.get("stop_loss_pct", 0.01)
        if side == "long":
            return entry_price * (1 - sl_pct)
        return entry_price * (1 + sl_pct)
        
    def _calculate_take_profit(self, side, entry_price):
        tp_pct = self.config.get("take_profit_pct", 0.02)
        if side == "long":
            return entry_price * (1 + tp_pct)
        return entry_price * (1 - tp_pct)
        
    def _calculate_liquidation_price(self, side, entry_price, size):
        # Simplified liquidation price estimation
        leverage = self.config.get("leverage", 10)
        if side == "long":
            return entry_price * (1 - 1/leverage + 0.005)  # 0.5% buffer
        return entry_price * (1 + 1/leverage - 0.005)
        
    def _calculate_pnl(self, position, exit_price):
        size = position["size"]
        entry = position["entry_price"]
        if position["side"] == "long":
            return ((exit_price - entry) / entry) * size
        return ((entry - exit_price) / entry) * size
        
    async def manage_risk(self):
        """Check and manage risk for all positions"""
        await self.sync()
        current_time = time.time()
        
        for symbol, position in list(self.positions.items()):
            # Get current price
            ticker = await self.api.fetch_ticker(symbol)
            if not ticker:
                continue
                
            current_price = ticker["last"]
            side = position["side"]
            
            # Check stop loss
            if ((side == "long" and current_price <= position["stop_loss"]) or
                (side == "short" and current_price >= position["stop_loss"])):
                await self._close_position(symbol, "stop loss", current_price)
                continue
                
            # Check take profit
            if ((side == "long" and current_price >= position["take_profit"]) or
                (side == "short" and current_price <= position["take_profit"])):
                await self._close_position(symbol, "take profit", current_price)
                continue
                
            # Check liquidation risk
            if ((side == "long" and current_price <= position["liquidation_price"]) or
                (side == "short" and current_price >= position["liquidation_price"])):
                await self._close_position(symbol, "liquidation risk", current_price)
                continue
                
            # Check max duration
            max_duration = self.config.get("max_position_duration", 1800)
            if current_time - position["entry_time"] > max_duration:
                await self._close_position(symbol, "duration limit", current_price)
                
    async def _close_position(self, symbol, reason, price):
        position = self.positions.get(symbol)
        if not position:
            return
            
        log.warning("Closing %s due to %s @ %s", symbol, reason, price)
        side = "sell" if position["side"] == "long" else "buy"
        await self.api.create_market_order(symbol, side, position["size"])
        self.record_exit(symbol, price)
