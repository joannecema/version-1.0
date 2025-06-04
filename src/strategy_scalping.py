import logging
from typing import Optional, Tuple

log = logging.getLogger("Scalping")

class ScalpingStrategy:
    def __init__(self, api, config: dict, tracker, executor) -> None:
        self.api = api
        self.config = config
        self.tracker = tracker
        self.executor = executor

        self.volume_multiplier = config.get("scalping_volume_multiplier", 1.5)
        self.momentum_period = config.get("scalping_momentum_period", 3)
        self.idle_exit_minutes = config.get("idle_exit_minutes", 5)
        self.min_roi = config.get("min_roi_threshold", 0.002)
        self.trailing_enabled = config.get("trailing_stop_enabled", True)
        self.strategy_name = "scalping"

    async def check_signal(self, symbol: str) -> Optional[Tuple[str, float]]:
        try:
            ohlcv = await self.api.fetch_ohlcv(symbol, timeframe="1m", limit=self.momentum_period + 1)
        except Exception as e:
            log.error(f"[{self.strategy_name.upper()}] ❌ Failed to fetch OHLCV for {symbol}: {e}")
            return None

        if not ohlcv or len(ohlcv) < self.momentum_period + 1:
            log.warning(f"[{self.strategy_name.upper()}] ⚠️ Not enough OHLCV for {symbol} (got {len(ohlcv)})")
            return None

        close_prices = [bar[4] for bar in ohlcv if isinstance(bar[4], (float, int))]
        if len(close_prices) < 2:
            log.warning(f"[{self.strategy_name.upper()}] ⚠️ Invalid close prices for {symbol}")
            return None

        momentum = close_prices[-1] - close_prices[0]
        percent_change = (momentum / close_prices[0]) if close_prices[0] else 0

        if abs(percent_change) < self.min_roi:
            log.debug(f"[{self.strategy_name.upper()}] ❌ No momentum for {symbol} (Δ={percent_change:.5f})")
            return None

        try:
            vol_now = ohlcv[-1][5]
            vol_prev = sum([bar[5] for bar in ohlcv[:-1]]) / len(ohlcv[:-1])
        except Exception as e:
            log.warning(f"[{self.strategy_name.upper()}] ⚠️ Volume error for {symbol}: {e}")
            return None

        if vol_now < vol_prev * self.volume_multiplier:
            log.debug(f"[{self.strategy_name.upper()}] ❌ Volume low on {symbol} ({vol_now:.2f} < {vol_prev * self.volume_multiplier:.2f})")
            return None

        side = "buy" if percent_change > 0 else "sell"

        try:
            if await self.tracker.has_open_position(symbol):
                log.info(f"[{self.strategy_name.upper()}] 🔄 Skipping {symbol} — already in position")
                return None

            capital = await self.tracker.get_available_usdt()
            size = self.executor._calculate_trade_size(capital, close_prices[-1])
            if size <= 0:
                log.warning(f"[{self.strategy_name.upper()}] ⚠️ Invalid size for {symbol}")
                return None
        except Exception as e:
            log.error(f"[{self.strategy_name.upper()}] ❌ Size calc failed for {symbol}: {e}")
            return None

        log.info(f"[{self.strategy_name.upper()}] ✅ Signal for {symbol} | Side={side.upper()} | Δ={percent_change:.4f} | Size={size}")
        return side, size
