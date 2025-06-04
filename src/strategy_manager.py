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
        self.daily_loss_limit = config.get("daily_loss_limit", 0.02)
        self.position_limits = config.get("position_limits", {})
        
        # Dynamic strategy loading with error handling
        self.strategies = {}
        self._load_strategies(config)

    def _load_strategies(self, config):
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
                    self.strategies[strategy_id] = strategy_class(
                        self.api, config, self.tracker, self.executor
                    )
                    log.info("Loaded strategy: %s", strategy_id)
                except Exception as e:
                    log.error("Failed to load strategy %s: %s", strategy_id, e, exc_info=True)

    async def execute(self):
        # Check risk limits before trading
        if not await self._check_risk_limits():
            return
            
        # Get symbols with liquidity filtering
        symbols = await self._get_filtered_symbols()
        if not symbols:
            return
            
        now = time.time()
        tasks = []
        
        for strategy_id in self.config.get("strategy_stack", []):
            strategy = self.strategies.get(strategy_id)
            if not strategy:
                continue
                
            for symbol in symbols:
                # Skip if symbol has position limits and they're reached
                if self._position_limit_reached(symbol):
                    continue
                    
                # Check strategy-symbol cooldown
                last_run = self.cooldowns[strategy_id].get(symbol, 0)
                cooldown = self.config.get("strategy_cooldowns", {}).get(strategy_id, 60)
                if now - last_run < cooldown:
                    continue
                    
                tasks.append(self._process_strategy_signal(strategy_id, strategy, symbol))
                
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            
    async def _check_risk_limits(self):
        """Check all risk limits before trading"""
        # Daily loss limit
        daily_pnl = await self.tracker.daily_pnl()
        if daily_pnl <= -self.daily_loss_limit:
            log.error("Daily loss limit reached (%.2f%%). Stopping trading.", daily_pnl * 100)
            return False
            
        # Max open positions
        if len(self.tracker.positions) >= self.max_open_positions:
            log.info("Max open positions reached (%d)", self.max_open_positions)
            return False
            
        return True
        
    async def _get_filtered_symbols(self):
        """Get symbols with volume and liquidity filtering"""
        try:
            return await self.api.get_top_symbols(
                count=self.config.get("symbols_count", 10),
                min_volume=self.config.get("min_symbol_volume", 1000000),
                min_liquidity=self.config.get("min_liquidity", 0.001),
                exclude_stable=True
            )
        except Exception as e:
            log.error("Failed to fetch symbols: %s", e, exc_info=True)
            return []
            
    def _position_limit_reached(self, symbol):
        """Check if symbol has position limits reached"""
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
            
            # Execute with risk checks
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
                
            # Update cooldown regardless of success
            self.cooldowns[strategy_id][symbol] = time.time()
                
        except Exception as e:
            log.exception("Error in %s for %s: %s", strategy_id, symbol, e)
