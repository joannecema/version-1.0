# strategy_manager.py
import logging
import asyncio
import time
import importlib
from collections import defaultdict

log = logging.getLogger("StrategyManager")

class StrategyManager:
    def __init__(self, config, api, tracker, executor):
        self.config = config
        self.api = api
        self.tracker = tracker
        self.executor = executor
        self.cooldowns = defaultdict(dict)
        self.strategy_weights = config.get("strategy_weights", {})
        self.max_open_positions = config.get("max_open_positions", 5)
        self.daily_loss_limit = config.get("daily_loss_limit", 0.02)
        self.position_limits = config.get("position_limits", {})
        self.strategies = self._load_strategies()

    def _load_strategies(self):
        strategies = {}
        strategy_mapping = {
            "scalping": ("src.strategy_scalping", "ScalpingStrategy"),
            "volume_breakout": ("src.strategy_breakout", "BreakoutStrategy"),
            "ema_rsi": ("src.strategy_ema_rsi", "EmaRsiStrategy"),
            "grid": ("src.strategy_grid", "GridStrategy"),
        }
        
        for strategy_id in self.config.get("strategy_stack", []):
            if strategy_id in strategy_mapping:
                module_name, class_name = strategy_mapping[strategy_id]
                try:
                    module = importlib.import_module(module_name)
                    strategy_class = getattr(module, class_name)
                    strategies[strategy_id] = strategy_class(
                        self.api, self.config, self.tracker, self.executor
                    )
                    log.info("Loaded strategy: %s", strategy_id)
                except Exception as e:
                    log.error("Failed to load strategy %s: %s", strategy_id, e)
        return strategies

    async def execute(self, symbols: list):
        if not await self._check_risk_limits():
            return
            
        now = time.time()
        tasks = []
        
        for strategy_id in self.config.get("strategy_stack", []):
            strategy = self.strategies.get(strategy_id)
            if not strategy:
                continue
                
            for symbol in symbols:
                if self._position_limit_reached(symbol):
                    continue
                    
                last_run = self.cooldowns[strategy_id].get(symbol, 0)
                cooldown = self.config.get("strategy_cooldowns", {}).get(strategy_id, 60)
                if now - last_run < cooldown:
                    continue
                    
                tasks.append(self._process_strategy_signal(strategy_id, strategy, symbol))
                
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            
    async def _check_risk_limits(self):
        daily_pnl = await self.tracker.daily_pnl()
        if daily_pnl <= -self.daily_loss_limit:
            log.error("Daily loss limit reached (%.2f%%)", daily_pnl * 100)
            return False
            
        if len(self.tracker.positions) >= self.max_open_positions:
            log.info("Max open positions reached (%d)", self.max_open_positions)
            return False
            
        return True
        
    def _position_limit_reached(self, symbol):
        if symbol in self.position_limits:
            position = self.tracker.get_open_position(symbol)
            if position and position["size"] >= self.position_limits[symbol]:
                return True
        return False
            
    async def _process_strategy_signal(self, strategy_id, strategy, symbol):
        try:
            signal = await strategy.check_signal(symbol)
            if not signal:
                return
                
            side, size = signal
            weight = self.strategy_weights.get(strategy_id, 1.0)
            adjusted_size = size * weight
            
            result = await self.executor.execute_order(
                symbol, 
                side, 
                adjusted_size,
                price_validation=True,
                strategy_id=strategy_id
            )
            
            if result and result.get("status") == "filled":
                self.tracker.record_entry(
                    symbol, 
                    side, 
                    result["filled_size"],
                    result["avg_price"],
                    strategy_id
                )
                log.info("Executed %s %s: %s @ %s", side.upper(), symbol, 
                         result["filled_size"], result["avg_price"])
                
            self.cooldowns[strategy_id][symbol] = time.time()
                
        except Exception as e:
            log.exception("Error in %s for %s: %s", strategy_id, symbol, e)
