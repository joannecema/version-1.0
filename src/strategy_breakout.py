import logging
from typing import Optional, Tuple

log = logging.getLogger("Breakout")

class BreakoutStrategy:
    def __init__(self, api, config, tracker, executor):
        self.api = api
        self.config = config
        self.tracker = tracker
        self.executor = executor

        self.timeframe = config.get("timeframe", "1m")
        self.lookback = int(config.get("breakout_lookback", 20))
        self.volume_multiplier = config.get("volume_multiplier", 2.0)
        self.min_roi = config.get("min_roi_threshold", 0.002)
        self.strategy_name = "volume_breakout"

    async def check_signal(self, symbol: str) -> Optional[Tuple[str, float]]:
        try:
            ohlcv = await self.api.fetch_ohlcv(symbol, self.timeframe, limit=self.lookback + 1)
        except Exception as e:
            log.error(f"[{self.strategy_name.upper()}] ❌ Failed to fetch OHLCV for {symbol}: {e}")
            return None

        if not ohlcv or len(ohlcv) < self.lookback + 1:
            log.warning(f"[{self.strategy_name.upper()}] ⚠️ Not enough OHLCV for {symbol} (got {len(ohlcv)})")
            return None

        try:
            highs = [c[2] for c in ohlcv[:-1]]
            lows = [c[3] for c in ohlcv[:-1]]
            volumes = [c[5] for c in ohlcv[:-1]]

            last_candle = ohlcv[-1]
            current_close = last_candle[4]
            current_high = last_candle[2]
            current_low = last_candle[3]
            current_volume = last_candle[5]

            max_high = max(highs)
            min_low = min(lows)
            avg_volume = sum(volumes) / len(volumes)

            # Volume confirmation
            if current_volume < avg_volume * self.volume_multiplier:
                log.debug(f"[{self.strategy_name.upper()}] ❌ Volume insufficient: {current_volume:.2f} < {avg_volume:.2f}")
                return None

            capital = await self.tracker.get_available_usdt()
            if capital <= 0:
                log.warning(f"[{self.strategy_name.upper()}] ⚠️ No USDT available for {symbol}")
                return None

            size = self.executor._calculate_trade_size(capital, current_close)
            if size <= 0:
                log.warning(f"[{self.strategy_name.upper()}] ⚠️ Invalid trade size for {symbol}")
                return None

            # ROI Check for strategy stacking (projected minimum)
            expected_roi = abs((current_high - min_low) / min_low)
            if expected_roi < self.min_roi:
                log.debug(f"[{self.strategy_name.upper()}] ❌ Expected ROI too low: {expected_roi:.4f} < {self.min_roi}")
                return None

            if await self.tracker.has_open_position(symbol):
                log.info(f"[{self.strategy_name.upper()}] 🔄 Skipping {symbol}, already in open position")
                return None

            if current_high > max_high:
                log.info(f"[{self.strategy_name.upper()}] ✅ BREAKOUT UP {symbol} | High: {current_high:.4f} > {max_high:.4f}")
                return "buy", size

            elif current_low < min_low:
                log.info(f"[{self.strategy_name.upper()}] ✅ BREAKOUT DOWN {symbol} | Low: {current_low:.4f} < {min_low:.4f}")
                return "sell", size

            log.debug(f"[{self.strategy_name.upper()}] ❌ No breakout detected on {symbol} | Close={current_close:.4f}")
            return None

        except Exception as e:
            log.error(f"[{self.strategy_name.upper()}] 💥 Signal calculation failed for {symbol}: {e}")
            return None
