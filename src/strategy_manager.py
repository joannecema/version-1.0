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

        try:
            from src.strategy_scalping import ScalpingStrategy
            from src.strategy_breakout import BreakoutStrategy
            from src.strategy_ema_rsi import EmaRsiStrategy
            from src.strategy_grid import GridStrategy
        except ImportError as e:
            log.error(f"[STRATEGY] ❌ Failed to import strategies: {e}")
            raise

        self.strategies = {
            "scalping": ScalpingStrategy(api, config, tracker, executor),
            "volume_breakout": BreakoutStrategy(api, config, tracker, executor),
            "ema_rsi": EmaRsiStrategy(api, config, tracker, executor),
            "grid": GridStrategy(api, config, tracker, executor)
        }

    async def execute(self):
        enabled = self.config.get("strategy_stack", [])
        symbols = await self.api.get_top_symbols(
            count=self.config.get("symbols_count", 10),
            exclude_stable=True
        )

        now = asyncio.get_event_loop().time()

        for strategy_name in enabled:
            strategy = self.strategies.get(strategy_name)
            if not strategy:
                log.warning(f"[STRATEGY] ❌ Strategy '{strategy_name}' not found or not initialized.")
                continue

            for symbol in symbols:
                cooldown_key = f"{strategy_name}:{symbol}"
                if self.cooldowns.get(cooldown_key, 0) > now:
                    log.info(f"[STRATEGY] ⏳ Cooldown active for {cooldown_key}")
                    continue

                try:
                    signal = await strategy.check_signal(symbol)
                    if signal:
                        side, size = signal

                        has_position = getattr(self.tracker, "has_open_position", lambda s: False)
                        if has_position(symbol):
                            log.info(f"[STRATEGY] 🔄 {symbol} already in open position — skipping {strategy_name}")
                            continue

                        result = await self.executor.execute_order(symbol, side, size)
                        if result:
                            self.tracker.record_entry(symbol, side, size, result.get("price"))
                        else:
                            log.warning(f"[STRATEGY] ⚠️ Order failed for {symbol} — applying cooldown")
                            self.cooldowns[cooldown_key] = now + 60  # 1-minute cooldown
                    else:
                        log.debug(f"[STRATEGY] ❌ No signal for {symbol} under {strategy_name}")

                except Exception as e:
                    log.error(f"[STRATEGY] 💥 Error in {strategy_name} on {symbol}: {e}")
                    self.cooldowns[cooldown_key] = now + 90  # 90s cooldown on error
