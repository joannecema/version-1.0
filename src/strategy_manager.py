import logging
import asyncio

log = logging.getLogger("StrategyManager")

class StrategyManager:
    def __init__(self, config, api, tracker, executor):
        self.config = config
        self.api = api
        self.tracker = tracker
        self.executor = executor
        self.cooldowns = {}

        # Strategy class mapping (ensure these are imported properly)
        from src.strategy_scalping import ScalpingStrategy
        from src.strategy_breakout import BreakoutStrategy
        from src.strategy_ema_rsi import EmaRsiStrategy
        from src.strategy_grid import GridStrategy

        self.strategies = {
            "scalping": ScalpingStrategy(api, config, tracker, executor),
            "volume_breakout": BreakoutStrategy(api, config, tracker, executor),
            "ema_rsi": EmaRsiStrategy(api, config, tracker, executor),
            "grid": GridStrategy(api, config, tracker, executor)
        }

    async def execute(self):
        enabled = self.config.get("strategy_stack", [])
        symbols = self.config.get("symbols", [])

        for strategy_name in enabled:
            strategy = self.strategies.get(strategy_name)
            if not strategy:
                log.warning(f"[STRATEGY] ❌ Strategy {strategy_name} not recognized.")
                continue

            for symbol in symbols:
                cooldown_key = f"{strategy_name}:{symbol}"
                if self.cooldowns.get(cooldown_key, 0) > asyncio.get_event_loop().time():
                    log.info(f"[STRATEGY] ⏳ Cooldown active for {cooldown_key}")
                    continue

                try:
                    signal = await strategy.check_signal(symbol)
                    if signal:
                        side, size = signal
                        if self.tracker.has_open_position(symbol):
                            log.info(f"[STRATEGY] 🔄 {symbol} already in open position — skipping {strategy_name}")
                            continue
                        result = await self.executor.execute_order(symbol, side, size)
                        if result:
                            self.tracker.record_entry(symbol, side, size, result['price'])
                        else:
                            log.warning(f"[STRATEGY] ⚠️ Order rejected or failed for {symbol}")
                            self.cooldowns[cooldown_key] = asyncio.get_event_loop().time() + 60  # 1-min cooldown
                    else:
                        log.debug(f"[STRATEGY] ❌ No signal for {symbol} in {strategy_name}")
                except Exception as e:
                    log.error(f"[STRATEGY] 💥 Error in {strategy_name} on {symbol}: {e}")
                    self.cooldowns[cooldown_key] = asyncio.get_event_loop().time() + 90  # backoff on error