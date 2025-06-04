# src/strategy_breakout.py

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

    async def check_signal(self, symbol: str) -> Optional[Tuple[str, float]]:
        try:
            ohlcv = await self.api.get_ohlcv(symbol, self.timeframe, self.lookback + 1)
        except Exception as e:
            log.error(f"[BREAKOUT] ❌ Failed to fetch OHLCV for {symbol}: {e}")
            return None

        if not ohlcv or len(ohlcv) < self.lookback + 1:
            log.warning(f"[BREAKOUT] ⚠️ Not enough OHLCV for {symbol}")
            return None

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
            log.debug(f"[BREAKOUT] Volume not sufficient on {symbol}: {current_volume:.2f} < {avg_volume:.2f}")
            return None

        # Breakout logic
        capital = await self.tracker.get_available_usdt()
        size = self.executor._calculate_trade_size(capital, current_close)

        if current_high > max_high:
            log.info(f"[BREAKOUT] ✅ BREAKOUT UP on {symbol} | {current_high:.4f} > {max_high:.4f}")
            return "buy", size
        elif current_low < min_low:
            log.info(f"[BREAKOUT] ✅ BREAKOUT DOWN on {symbol} | {current_low:.4f} < {min_low:.4f}")
            return "sell", size

        log.debug(f"[BREAKOUT] ❌ No breakout on {symbol} | Price={current_close:.4f}")
        return None
