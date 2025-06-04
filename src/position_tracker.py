# position_tracker.py
import logging
import time
from datetime import datetime, timedelta

log = logging.getLogger("PositionTracker")

class PositionTracker:
    def __init__(self, config, api):
        self.config = config
        self.api = api
        self.positions = {}
        self.trade_history = []
        self.daily_start_balance = None
        self.last_balance_sync = 0
        self.balance_sync_interval = 300  # 5 minutes
        
    async def sync_balance(self):
        current_time = time.time()
        if current_time - self.last_balance_sync > self.balance_sync_interval:
            try:
                balance = await self.api.fetch_balance()
                self.available_balance = balance['USDT']['free']
                if not self.daily_start_balance:
                    self.daily_start_balance = self.available_balance
                self.last_balance_sync = current_time
            except Exception as e:
                log.error("Balance sync failed: %s", e)
                
    async def daily_pnl(self):
        await self.sync_balance()
        if not self.daily_start_balance or self.daily_start_balance == 0:
            return 0
        return (self.available_balance - self.daily_start_balance) / self.daily_start_balance

    def record_entry(self, symbol, side, size, price, strategy, timestamp=None):
        self.positions[symbol] = {
            "symbol": symbol,
            "side": side,
            "size": size,
            "entry_price": price,
            "entry_time": timestamp or time.time(),
            "strategy": strategy,
            "stop_loss": self._calculate_stop_loss(side, price),
            "take_profit": self._calculate_take_profit(side, price)
        }
        log.info("Entry: %s %s %s @ %s", symbol, side.upper(), size, price)

    def record_exit(self, symbol, exit_price, exit_time=None):
        position = self.positions.pop(symbol, None)
        if not position:
            return
            
        pnl = self._calculate_pnl(
            position["entry_price"],
            exit_price,
            position["size"],
            position["side"]
        )
        
        trade = {
            **position,
            "exit_price": exit_price,
            "exit_time": exit_time or time.time(),
            "pnl": pnl,
            "duration": (exit_time or time.time()) - position["entry_time"]
        }
        
        self.trade_history.append(trade)
        log.info("Exit: %s @ %s | PnL: %.4f%%", symbol, exit_price, pnl*100)

    def _calculate_stop_loss(self, side, entry_price):
        if side == "long":
            return entry_price * (1 - self.config.get("stop_loss_pct", 0.01))
        return entry_price * (1 + self.config.get("stop_loss_pct", 0.01))
        
    def _calculate_take_profit(self, side, entry_price):
        if side == "long":
            return entry_price * (1 + self.config.get("take_profit_pct", 0.02))
        return entry_price * (1 - self.config.get("take_profit_pct", 0.02))
        
    def _calculate_pnl(self, entry, exit, size, side):
        if side == "long":
            return ((exit - entry) / entry) * size
        return ((entry - exit) / entry) * size

    async def reconcile_positions(self):
        """Sync with exchange to fix any discrepancies"""
        try:
            exchange_positions = await self.api.fetch_positions()
            for symbol, pos in list(self.positions.items()):
                if symbol not in exchange_positions:
                    log.warning("Position missing on exchange: %s", symbol)
                    self.positions.pop(symbol, None)
                    
            # Add any positions missing locally
            for symbol, pos in exchange_positions.items():
                if symbol not in self.positions:
                    log.warning("Found unexpected position: %s", symbol)
                    self.record_entry(
                        symbol,
                        pos["side"],
                        pos["size"],
                        pos["entry_price"],
                        "reconciled"
                    )
        except Exception as e:
            log.error("Position reconciliation failed: %s", e)

    async def manage_risk(self):
        current_time = time.time()
        for symbol, pos in list(self.positions.items()):
            # Get current price
            ticker = await self.api.fetch_ticker(symbol)
            if not ticker:
                continue
                
            current_price = ticker["last"]
            
            # Check stop loss
            if ((pos["side"] == "long" and current_price <= pos["stop_loss"]) or
                (pos["side"] == "short" and current_price >= pos["stop_loss"])):
                log.warning("Stop loss triggered for %s @ %s", symbol, current_price)
                await self.api.create_market_order(symbol, "sell" if pos["side"] == "long" else "buy", pos["size"])
                self.record_exit(symbol, current_price)
                continue
                
            # Check take profit
            if ((pos["side"] == "long" and current_price >= pos["take_profit"]) or
                (pos["side"] == "short" and current_price <= pos["take_profit"])):
                log.info("Take profit triggered for %s @ %s", symbol, current_price)
                await self.api.create_market_order(symbol, "sell" if pos["side"] == "long" else "buy", pos["size"])
                self.record_exit(symbol, current_price)
                continue
                
            # Check max duration
            max_duration = self.config.get("max_position_duration", 1800)  # 30 minutes
            if current_time - pos["entry_time"] > max_duration:
                log.info("Closing %s due to duration limit", symbol)
                await self.api.create_market_order(symbol, "sell" if pos["side"] == "long" else "buy", pos["size"])
                self.record_exit(symbol, current_price)
