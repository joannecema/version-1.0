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
                log.warning(f"[STRATEGY] ❌ Strategy {strategy_name} not found in map.")
                continue

            log.info(f"[STRATEGY] 🧠 Running {strategy_name} strategy…")

            for symbol in symbols:
                cooldown_key = f"{strategy_name}:{symbol}"
                if self.cooldowns.get(cooldown_key, 0) > now:
                    log.debug(f"[STRATEGY] ⏳ Skipping {symbol} in {strategy_name} due to cooldown")
                    continue

                try:
                    signal = await strategy.check_signal(symbol)
                    if signal:
                        side, size = signal

                        if getattr(self.tracker, "has_open_position", lambda s: False)(symbol):
                            log.info(f"[STRATEGY] 🔁 Skipped {symbol} — already in position for {strategy_name}")
                            continue

                        result = await self.executor.execute_order(symbol, side, size)
                        if result:
                            self.tracker.record_entry(symbol, side, size, result.get("price"))
                            log.info(f"[STRATEGY] ✅ Executed {side.upper()} on {symbol} with size {size}")
                        else:
                            log.warning(f"[STRATEGY] ⚠️ Order rejected for {symbol} in {strategy_name}")
                            self.cooldowns[cooldown_key] = now + 60
                    else:
                        log.debug(f"[STRATEGY] ❌ No signal for {symbol} in {strategy_name}")
                except Exception as e:
                    log.error(f"[STRATEGY] 💥 Exception in {strategy_name} for {symbol}: {e}")
                    self.cooldowns[cooldown_key] = now + 90
