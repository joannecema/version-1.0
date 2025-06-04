# strategy_manager.py
import logging
import asyncio
import time
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
        self.daily_loss_limit = config.get("daily_loss_limit", 0.02)  # 2%
        
        # Dynamic strategy loading
        self.strategies = {}
        strategy_mapping = {
            "scalping": "ScalpingStrategy",
            "volume_breakout": "BreakoutStrategy",
            "ema_rsi": "EmaRsiStrategy",
            "grid": "GridStrategy",
        }
        
        for strategy_id, class_name in strategy_mapping.items():
            if strategy_id in config.get("strategy_stack", []):
                try:
                    module = __import__(f"src.strategy_{strategy_id}", fromlist=[class_name])
                    strategy_class = getattr(module, class_name)
                    self.strategies[strategy_id] = strategy_class(api, config, tracker, executor)
                    log.info("Loaded strategy: %s", strategy_id)
                except Exception as e:
                    log.error("Failed to load strategy %s: %s", strategy_id, e)

    async def execute(self):
        # Check daily loss limit before trading
        if await self.tracker.daily_pnl() <= -self.daily_loss_limit:
            log.error("Daily loss limit reached. Stopping trading.")
            return
            
        enabled_strategies = self.config.get("strategy_stack", [])
        if not enabled_strategies:
            return
            
        symbols = await self.api.get_top_symbols(
            count=self.config.get("symbols_count", 10),
            min_volume=1000000,  # $1M daily volume
            exclude_stable=True
        )
        
        # Exit if at position limit
        if len(self.tracker.positions) >= self.max_open_positions:
            log.debug("Max open positions reached (%d)", self.max_open_positions)
            return
            
        now = time.time()
        tasks = []
        
        for strategy_id in enabled_strategies:
            strategy = self.strategies.get(strategy_id)
            if not strategy:
                continue
                
            for symbol in symbols:
                # Check strategy-symbol cooldown
                last_run = self.cooldowns[strategy_id].get(symbol, 0)
                cooldown = self.config.get("strategy_cooldowns", {}).get(strategy_id, 60)
                if now - last_run < cooldown:
                    continue
                    
                tasks.append(self._process_strategy_signal(strategy_id, strategy, symbol))
                
        if tasks:
            await asyncio.gather(*tasks)
            
    async def _process_strategy_signal(self, strategy_id, strategy, symbol):
        try:
            signal = await strategy.check_signal(symbol)
            if not signal:
                return
                
            side, size = signal
            weight = self.strategy_weights.get(strategy_id, 1.0)
            adjusted_size = size * weight
            
            # Execute with price validation
            result = await self.executor.execute_order(
                symbol, 
                side, 
                adjusted_size,
                price_validation=True
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
                
            # Update cooldown regardless of success
            self.cooldowns[strategy_id][symbol] = time.time()
                
        except Exception as e:
            log.exception("Error processing %s for %s: %s", strategy_id, symbol, e)
