import logging
from src.utils import calculate_sma

class ScalpingStrategy:
    def __init__(self, api, tracker, executor, cfg):
        self.api = api
        self.tracker = tracker
        self.executor = executor
        self.cfg = cfg
        self.logger = logging.getLogger("ScalpingStrategy")

        self.timeframe = cfg.get("timeframe", "1m")
        self.lookback = int(cfg.get("lookback", 50))
        self.sma_short_period = int(cfg.get("sma_short", 20))
        self.sma_long_period = int(cfg.get("sma_long", 50))

    async def check_and_trade(self, symbol: str):
        try:
            ohlcv = await self.api.get_ohlcv(symbol, self.timeframe, self.lookback + 1)
            if not ohlcv or len(ohlcv) < self.lookback + 1:
                self.logger.warning(f"[SCALP] ❌ Not enough OHLCV for {symbol} (have {len(ohlcv)}, need {self.lookback + 1})")
                return

            closes = [bar[4] for bar in ohlcv if bar and isinstance(bar[4], (int, float))]
            if len(closes) < max(self.sma_short_period, self.sma_long_period):
                self.logger.warning(f"[SCALP] ⚠️ Insufficient closing prices for SMA calculation on {symbol}")
                return

            sma_short = calculate_sma(closes, self.sma_short_period)
            sma_long = calculate_sma(closes, self.sma_long_period)
            current_price = closes[-1]

            if sma_short is None or sma_long is None:
                self.logger.warning(f"[SCALP] ❌ SMA calculation failed for {symbol}")
                return

            self.logger.debug(
                f"[SCALP] {symbol} price={current_price:.4f} | SMA{self.sma_short_period}={sma_short:.4f} | SMA{self.sma_long_period}={sma_long:.4f}"
            )

            # ENTRY condition
            if sma_short > sma_long and not self.tracker.has_position(symbol):
                self.logger.info(f"[SCALP] ✅ Enter LONG {symbol} @ {current_price:.4f}")
                await self.executor.enter_long(symbol, current_price)

            # EXIT condition
            elif sma_short < sma_long:
                if self.tracker.has_long(symbol):
                    self.logger.info(f"[SCALP] ❌ Exit LONG {symbol} @ {current_price:.4f}")
                    await self.executor.exit_position(symbol, current_price)
                else:
                    self.logger.debug(f"[SCALP] ⏩ No active long to exit for {symbol}")

        except Exception as e:
            self.logger.error(f"[SCALP] ❌ Error during check_and_trade for {symbol}: {e}")